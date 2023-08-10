import os
import json
import logging

from django.utils.translation import gettext as _
from django.utils.html import escape
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.conf import settings
from django.contrib import messages
from django.http import (
    HttpResponse,
    HttpResponseRedirect,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    Http404
)
from django.core.exceptions import ValidationError
from django.contrib.staticfiles import finders
from django.contrib.auth.decorators import login_required

from mglib.pdfinfo import get_pagecount
from mglib.step import Step
from mglib.shortcuts import extract_img

from papermerge.core.storage import default_storage
from papermerge.core.lib.hocr import Hocr
from .decorators import json_response, require_PERM

from papermerge.core.models import (
    Folder,
    Document,
    Page,
    BaseTreeNode,
    Access
)
from papermerge.core.utils import filter_node_id
from papermerge.core import signal_definitions as signals
from papermerge.core.import_pipeline import WEB, go_through_pipelines
from papermerge.core.tasks import ocr_page

logger = logging.getLogger(__name__)


@login_required
def document(request, doc_id):
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        return render(request, "admin/document_404.html")

    nodes_perms = request.user.get_perms_dict(
        [doc], Access.ALL_PERMS
    )

    version = request.GET.get('version', None)
    if version is not None:
        version = int(version)

    if doc.is_latest_version(version):
        pagelist = doc.pages.all()
    else:
        pagelist = [{
            'id': page_num,
            'number': page_num
        } for page_num in range(1, doc.get_pagecount(version=version) + 1)]

    # request.is_ajax is repricated since Django 3.1
    if not (request.headers.get('x-requested-with') == 'XMLHttpRequest'):
        if request.user.has_perm(Access.PERM_READ, doc):
            if not doc.is_latest_version(version):
                messages.info(
                    request, _(
                        "This is past version of the document."
                        " Content of this version is read only."
                    )
                )
            return render(
                request,
                'admin/document.html',
                {
                    'pages': pagelist,
                    'tags': doc.tags.all(),
                    'document': doc,
                    'versions': doc.get_versions(),
                    'is_latest_version': doc.is_latest_version(version),
                    'version': version,
                    'has_perm_write': nodes_perms[doc.id].get(
                        Access.PERM_WRITE, False
                    ),
                }
            )
        else:
            return HttpResponseForbidden()

    # ajax + PATCH
    if request.method == 'PATCH':
        # test_document_view
        # TestDocumentAjaxOperationsView.test_update_notes
        # test_update_notes
        if request.user.has_perm(Access.PERM_WRITE, doc):
            data = json.loads(request.body)
            if 'notes' in data:
                # dangerous user input. Escape it.
                doc.notes = escape(data['notes'])
                doc.save()
                return HttpResponse(
                    json.dumps(
                        {
                            'msg': _("Notes saved!")
                        }
                    ),
                    content_type="application/json",
                )
        else:
            return HttpResponseForbidden(
                json.dumps({'msg': _("Access denied")}),
                content_type="application/json",
            )

    if request.method == 'DELETE':
        # test_document_view
        # TestDocumentAjaxOperationsView.test_delete_document
        if request.user.has_perm(Access.PERM_DELETE, doc):
            doc.delete()
            return HttpResponse(
                json.dumps(
                    {
                        'msg': "OK",
                        'url': reverse('admin:browse')
                    }
                ),
                content_type="application/json",
            )
        else:
            return HttpResponseForbidden(
                json.dumps(_("Access denied")),
                content_type="application/json",
            )

    # so, ajax only here
    if request.method == 'POST':
        # ajax + post
        pass

    # ajax + GET here
    result_dict = doc.to_dict()
    result_dict['user_perms'] = nodes_perms[doc.id]

    response = HttpResponse(
        json.dumps({'document': result_dict}),
        content_type="application/json",
    )

    response['Vary'] = 'Accept'

    return response


@json_response
@login_required
@require_POST
def cut_node(request):
    data = json.loads(request.body)
    node_ids = [item['id'] for item in data]
    nodes = BaseTreeNode.objects.filter(
        id__in=node_ids
    )
    nodes_perms = request.user.get_perms_dict(
        nodes, Access.ALL_PERMS
    )
    for node in nodes:
        if not nodes_perms[node.id].get(
            Access.PERM_DELETE, False
        ):
            msg = _(
                "%s does not have permission to cut %s"
            ) % (request.user.username, node.title)

            return msg, HttpResponseForbidden.status_code

    # request.clipboard.nodes = request.nodes
    request.nodes.add(node_ids)

    return 'OK'


@login_required
def clipboard(request):
    if request.method == 'GET':

        return HttpResponse(
            # request.nodes = request.clipboard.nodes
            json.dumps({'clipboard': request.nodes.all()}),
            content_type="application/json",
        )

    return HttpResponse(
        json.dumps({'clipboard': []}),
        content_type="application/json",
    )


@login_required
@require_POST
def paste_pages(request):
    """
    Paste pages in a changelist view.
    This means a new document instance
    is created.
    """
    data = json.loads(request.body)
    parent_id = data.get('parent_id', None)

    if parent_id:
        parent_id = int(parent_id)

    Document.paste_pages(
        user=request.user,
        parent_id=parent_id,
        doc_pages=request.pages.all()
    )

    request.pages.clear()

    return HttpResponse(
        json.dumps({'msg': 'OK'}),
        content_type="application/json",
    )


@login_required
@require_POST
def paste_node(request):
    data = json.loads(request.body)

    if not data:
        return HttpResponseBadRequest(
            json.dumps({
                'msg': 'Payload empty'
            }),
            content_type="application/json"
        )

    parent_id = data.get('parent_id', False)

    if parent_id:
        parent = BaseTreeNode.objects.filter(id=parent_id).first()
    else:
        parent = None

    # request.clipboard.nodes = request.nodes
    node_ids = request.nodes.all()

    # iterate through all node ids and change their
    # parent to new one (parent_id)
    for node in BaseTreeNode.objects.filter(id__in=node_ids):
        node.refresh_from_db()
        if parent:
            parent.refresh_from_db()
        Document.objects.move_node(node, parent)

    # request.clipboard.nodes = request.nodes
    request.nodes.clear()

    return HttpResponse(
        json.dumps({
            'msg': 'OK'
        }),
        content_type="application/json"
    )


@json_response
@login_required
def rename_node(request, id):
    """
    Renames a node (changes its title field).
    """

    data = json.loads(request.body)
    title = data.get('title', None)
    node = get_object_or_404(BaseTreeNode, id=id)

    if not request.user.has_perm(Access.PERM_WRITE, node):
        msg = _(
            "You don't have permissions to rename this document"
        )
        return msg, HttpResponseForbidden.status_code

    if not title:
        return _('Missing title')

    node.title = title
    # never trust data coming from user
    try:
        node.full_clean()
    except ValidationError as e:
        return e.message_dict, HttpResponseBadRequest.status_code

    node.save()

    return 'OK'


@login_required
@require_POST
@require_PERM('core.add_folder')
def create_folder(request):
    """
    Creates a new folder.

    Mandatory parameters parent_id and title:
    * If either parent_id or title are missing - does nothing.
    * If parent_id < 0 => creates a folder with parent root.
    * If parent_id >= 0 => creates a folder with given parent id.
    """
    data = json.loads(request.body)
    parent_id = data.get('parent_id', -1)
    title = data.get('title', False)

    if title == Folder.INBOX_NAME:
        return HttpResponseBadRequest(
            json.dumps({
                'msg': 'This title is not allowed'
            }),
            content_type="application/json"
        )

    if not (parent_id or title):
        logger.info(
            "Invalid params for create_folder: parent=%s title=%s",
            parent_id,
            title
        )
        return HttpResponseBadRequest(
            json.dumps({
                'msg': 'Both parent_id and title empty'
            }),
            content_type="application/json"
        )
    try:
        parent_id = int(parent_id or -1)
    except ValueError:
        parent_id = -1

    if int(parent_id) < 0:
        parent_folder = None
    else:
        parent_folder = Folder.objects.filter(id=parent_id).first()
        # if not existing parent_id was given, redirect to root
        if not parent_folder:
            return HttpResponseBadRequest(
                json.dumps({
                    'msg': f"Parent with id={parent_id} does not exist"
                }),
                content_type="application/json"
            )
    folder = Folder(
        title=title,
        parent=parent_folder,
        user=request.user
    )
    try:
        folder.full_clean()
    except ValidationError as e:
        # create human friednly error message from
        # dictionary
        err_msg = " ".join([
            f"{k}: {' '.join(v)}" for k, v in e.message_dict.items()
        ])
        return HttpResponseBadRequest(
            json.dumps({
                'msg': err_msg
            }),
            content_type="application/json"
        )
    # save folder only after OK validation
    folder.save()
    signals.folder_created.send(
        sender='core.views.documents.create_folder',
        user_id=request.user.id,
        level=logging.INFO,
        message=_("Folder created"),
        folder_id=folder.id
    )

    return HttpResponse(
        json.dumps(
            folder.to_dict()
        )
    )


@json_response
@login_required
@require_POST
def upload(request):
    print(' # upload # '*50)
    """
    To understand returned value, have a look at
    papermerge.core.views.decorators.json_reponse decorator
    """
    files = request.FILES.getlist('file')
    if not files:
        logger.warning(
            "POST request.FILES is empty. Forgot adding file?"
        )
        return "Missing input file", 400

    if len(files) > 1:
        msg = "More then one files per ajax? how come?"
        logger.warning(msg)

        return msg, 400

    f = files[0]

    logger.debug("upload for f=%s user=%s", f, request.user)
    user = request.user
    parent_id = request.POST.get('parent', "-1")
    # print('parent id', parent_id)
    parent_id = filter_node_id(parent_id)

    lang = request.POST.get('language')
    notes = request.POST.get('notes')

    init_kwargs = {'payload': f, 'processor': WEB}

    apply_kwargs = {
        'user': user,
        'name': f.name,
        'parent': parent_id,
        'lang': lang,
        'notes': notes,
        'apply_async': True
    }

    try:
        print('start'*50)
        doc = go_through_pipelines(init_kwargs, apply_kwargs)
        print('end'*50)
    except ValidationError as error:
        return str(error), 400

    if not doc:
        status = 400
        msg = _(
            "File type not supported."
            " Only pdf, tiff, png, jpeg files are supported"
        )
        return msg, status

    # after each upload return a json object with
    # following fields:
    #
    # - title
    # - preview_url
    # - doc_id
    # - action_url  -> needed for renaming/deleting selected item
    #
    # with that info a new thumbnail will be created.
    preview_url = reverse(
        'core:preview', args=(doc.id, 200, 1)
    )

    result = {
        'title': doc.title,
        'doc_id': doc.id,
        'action_url': "",
        'preview_url': preview_url
    }
    print("result"*50 , result)

    return result


@login_required
def usersettings(request, option, value):

    if option == 'documents_view':
        user_settings = request.user.preferences
        if value in ('list', 'grid'):
            user_settings['views__documents_view'] = value
            user_settings['views__documents_view']

    return HttpResponseRedirect(
        request.META.get('HTTP_REFERER')
    )


@login_required
def hocr(request, id, step=None, page="1"):

    logger.debug(f"hocr for doc_id={id}, step={step}, page={page}")
    try:
        doc = Document.objects.get(id=id)
    except Document.DoesNotExist:
        raise Http404("Document does not exists")

    version = request.GET.get('version', None)
    doc_path = doc.path(version=version)

    if request.user.has_perm(Access.PERM_READ, doc):
        # document absolute path
        doc_abs_path = default_storage.abspath(doc_path.url())
        if not os.path.exists(
            doc_abs_path
        ):
            raise Http404("HOCR data not yet ready.")

        page_count = get_pagecount(doc_abs_path)
        if page > page_count or page < 0:
            raise Http404("Page does not exists")

        page_path = doc.page_paths(version=version)[page]
        hocr_abs_path = default_storage.abspath(page_path.hocr_url())

        logger.debug(f"Extract words from {hocr_abs_path}")

        if not os.path.exists(hocr_abs_path):
            default_storage.download(
                page_path.hocr_url()
            )

        if not os.path.exists(hocr_abs_path):
            raise Http404("HOCR data not yet ready.")

        # At this point local HOCR data should be available.
        hocr = Hocr(
            hocr_file_path=hocr_abs_path
        )

        return HttpResponse(
            json.dumps({
                'hocr': hocr.good_json_words(),
                'hocr_meta': hocr.get_meta()
            }),
            content_type="application/json",
        )

    return HttpResponseForbidden()


@login_required
def preview(request, id, step=None, page="1"):
    print('PREVIEW start')
    try:
        doc = Document.objects.get(id=id) 
        print('doc-->>>'*25, doc)
    except Document.DoesNotExist:
        raise Http404("Document does not exists")

    if request.user.has_perm(Access.PERM_READ, doc):
        version = request.GET.get('version', None)
        print('version: ', version)

        page_path = doc.get_page_path(
            page_num=page,
            step=Step(step),
            version=version
        )
        print('page_path: ', page_path)
        img_abs_path = default_storage.abspath(
            page_path.img_url()
        )
        print('img_abs_path: ', img_abs_path)
        print(' page_path.img_url(): ',  page_path.img_url())

        if not os.path.exists(img_abs_path):
            print('no path $$$$$$$$$$$$$$$$')
            logger.debug(
                f"Preview image {img_abs_path} does not exists. Generating..."
            )
            print('!!!!!!!!!!!!!!!!!!!!!!!')
            print(settings.MEDIA_ROOT)
            print(page_path)
            extract_img(
                page_path, media_root=settings.MEDIA_ROOT
            )
            print('############# #')

        try:
            with open(img_abs_path, "rb") as f:
                return HttpResponse(f.read(), content_type="image/jpeg")
        except IOError:
            generic_file = "admin/img/document.png"
            if Step(step).is_thumbnail:
                generic_file = "admin/img/document_thumbnail.png"

            file_path = finders.find(generic_file)

            with open(file_path, "rb") as f:
                return HttpResponse(f.read(), content_type="image/png")

    return HttpResponseForbidden()


@json_response
@login_required
def text_view(
    request,
    id,
    document_version,
    page_number
):

    try:
        page = Page.objects.get(
            document__id=id,
            number=page_number
        )
    except Page.DoesNotExist:
        raise Http404("Page does not exists")

    doc = page.document

    if request.user.has_perm(Access.PERM_READ, doc):
        txt_abs_path = default_storage.abspath(
            page.path(version=document_version).txt_url()
        )
        text = ""

        with open(txt_abs_path, "r") as f:
            text = f.read()

        return {
            'page_text': text,
            'page_number': page.number,
            'document_version': document_version
        }

    msg = _(
        "%s does not have read perissions on %s"
    ) % (request.user.username, doc.title)

    return msg, HttpResponseForbidden.status_code


@json_response
@login_required
@require_POST
def run_ocr_view(request):
    print('%%%%%%%%%%%%%% run_ocr_view %%%%%%%%%%%%%%')


    post_data = json.loads(request.body)
    print(f"==>> post_data: {post_data}")
    node_ids = post_data['document_ids']
    new_lang = post_data['lang']

    documents = Document.objects.filter(
        id__in=node_ids
    )
    print(f"==>> documents: {documents}")
    nodes_perms = request.user.get_perms_dict(
        documents, Access.ALL_PERMS
    )
    for node in documents:
        if not nodes_perms[node.id].get(
            Access.PERM_WRITE, False
        ):
            msg = _(
                "%s does not have write perission on %s"
            ) % (request.user.username, node.title)

            return msg, HttpResponseForbidden.status_code

    for doc in documents:
        old_version = doc.version
        new_version = doc.version + 1

        default_storage.copy_doc(
            src=doc.path(version=old_version),
            dst=doc.path(version=new_version)
        )
        for page_num in range(1, doc.page_count + 1):
            ocr_page.apply_async(kwargs={
                'user_id': doc.user.id,
                'document_id': doc.id,
                'file_name': doc.file_name,
                'page_num': page_num,
                'lang': new_lang,
                'namespace': getattr(default_storage, 'namespace', None),
                'version': new_version
            })

        doc.lang = new_lang
        doc.version = new_version
        doc.save()

    return {'msg': _("OCR process successfully started")}
