from os.path import getsize, basename
import logging
from magic import from_file
from tempfile import _TemporaryFileWrapper

from django.core.files.temp import NamedTemporaryFile
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import TemporaryUploadedFile
from django.utils import module_loading

from mglib.pdfinfo import get_pagecount
from mglib.exceptions import FileTypeNotSupported

from papermerge.core.models import (
    Folder, Document, User
)
from papermerge.core.storage import default_storage
from papermerge.core.tasks import ocr_page
from papermerge.core import signal_definitions as signals
from papermerge.core.ocr import COMPLETE, STARTED
from papermerge.core.utils import Timer

logger = logging.getLogger(__name__)


REST_API = "REST_API"
WEB = "WEB"
IMAP = "IMAP"
LOCAL = "LOCAL"


class DefaultPipeline:
    """
    Default Pipeline class. It is meant to be extended by apps. Most commonly
    the methods check_mimetype (remember to change get_pagecount as well) and
    apply should be modified. All checks whether the payload is compatible
    with an extended pipeline is to be done  in the init method, the apply
    method is not expected to raise exceptions.
    """

    def __init__(
        self,
        payload=None,
        doc=None,
        processor=WEB,
        **kwargs
    ):
        """
        Init method of the pipeline. Only succeeds if the file is compatible
        with the pipeline.

        Args:
            payload (Union[bytes, TemporaryUploadedFile,
            _TemporaryFileWrapper], optional): payload to be ingested.
            Defaults to None. doc (Document, optional): document to be
            updated. Defaults to None. processor (str, optional): from which
            importer this class is invocated. Defaults to WEB.

        Raises:
            TypeError: raised when payload is not a supported file object
            FileTypeNotSupported: raised when payload is of a wrong mimetype
            for the pipeline
        """
        self.processor = processor
        self.doc = doc
        print('payload!!!!!!!!!!!!!!!!!!!!!!!', payload)
        if payload is None:
            print('Nonecc'*25)
            raise TypeError
        elif isinstance(payload, bytes):
            print('elif '*50)
            payload = self.write_temp(payload)

        self.payload = payload
        print('payloadxxxxxxxxxxxxxx', payload)

        if isinstance(payload, TemporaryUploadedFile):
            print('yes'*25)
            self.path = payload.temporary_file_path()
            print('path', self.path)
        elif isinstance(payload, _TemporaryFileWrapper):
            print('no'*25)
            self.path = payload.name
        else:
            raise TypeError

        self.check_mimetype()

    def check_mimetype(self):
        """Check if mimetype of the document to be imported is supported
        by Papermerge or one of its apps.

        Raises:
            FileTypeNotSupported: If the mimetype is not supported by this
            pipeline

        """
        supported_mimetypes = settings.PAPERMERGE_MIMETYPES
        print('~-+++++++++++++++', supported_mimetypes)
        mime = from_file(self.path, mime=True)
        print('mime@@@@@@@@@@@@@@@@', mime)
        if mime in supported_mimetypes:
            return None
        raise FileTypeNotSupported

    def write_temp(self, payload):
        """Write a temporary file to disk, necessary for certain
        payload types that are not stored on file.

        Args:
            payload (bytes): ingested payload

        Returns:
            temp (NamedTemporaryFile): temporary file on disk
        """
        print('~~~~'*50)
        logger.debug(
            f"{self.processor} importer: creating temporary file"
        )

        temp = NamedTemporaryFile()
        print('temp55'*50, temp)
        temp.write(payload)
        temp.flush()
        return temp

    @staticmethod
    def get_user_properties(user):
        """Get properties of the document owner, if no owner is specified
        the document gets assigned to first superuser

        Args:
            user (User): owner object

        Returns:
            user (User): owner object
            lang (str): user language
            inbox (Folder): inbox folder
        """
        if user is None:
            user = User.objects.filter(
                is_superuser=True
            ).first()
        if isinstance(user, str):
            user = User.objects.filter(
                username=user
            ).first()
        lang = user.preferences['ocr__OCR_Language']

        inbox, _ = Folder.objects.get_or_create(
            title=Folder.INBOX_NAME,
            parent=None,
            user=user
        )
        return user, lang, inbox

    def move_tempfile(self, doc):
        print('move temp fileeeeeeeeeeeeeeeeeeeeeeeeeee')
        print('default_storage', default_storage)
        print('srccccc', self.path)
        print('dst', doc.path().url())
        default_storage.copy_doc(
            src=self.path,
            dst=doc.path().url()
        )
        print('')
        return None

    def page_count(self):
        return get_pagecount(self.path)

    def ocr_document(
        self,
        document,
        page_count,
        lang
    ):
        user_id = document.user.id
        document_id = document.id
        file_name = document.file_name

        logger.debug(
            f"{self.processor} importer: "
            f"document {document_id} has {page_count} pages."
        )
        for page_num in range(1, page_count + 1):
            signals.page_ocr.send(
                sender='worker',
                level=logging.INFO,
                message="",
                user_id=user_id,
                document_id=document_id,
                page_num=page_num,
                lang=lang,
                status=STARTED
            )

            with Timer() as time:
                ocr_page(
                    user_id=user_id,
                    document_id=document_id,
                    file_name=file_name,
                    page_num=page_num,
                    lang=lang,
                )

            msg = "{} importer: OCR took {} seconds to complete.".format(
                self.processor,
                time
            )
            signals.page_ocr.send(
                sender='worker',
                level=logging.INFO,
                message=msg,
                user_id=user_id,
                document_id=document_id,
                page_num=page_num,
                lang=lang,
                status=COMPLETE
            )

    def get_init_kwargs(self):
        """Propagates keyword arguments to use in the init method
        of donwstream pipelines. Should be overwritten by inheriting
        classes.

        Returns:
            A dict with the generated document or None if no document
            could be generated
        """
        if self.doc:
            return {'doc': self.doc}
        return None

    def get_apply_kwargs(self):
        """Propagates keyword arguments to use in the apply method
        of donwstream pipelines. Should be overwritten by inheriting
        classes.

        Returns:
            None
        """
        return None

    def apply(
        self,
        user=None,
        parent=None,
        lang=None,
        notes=None,
        name=None,
        skip_ocr=False,
        apply_async=False,
        create_document=True,
        **kwargs
    ):
        """
        Apply the pipeline. The document is created or modified here.  This
method is not supposed to throw errors.

        Arguments:
        - user (User, optional): document owner.
        - parent (Folder, optional): folder containing the document.
        - lang (str, optional): OCR language.
        - notes (str, optional): document notes.
        - name (str, optional): document name.
        - skip_ocr (bool, optional):
            whether to skip OCR processing. Defaults to False.
        - apply_async (bool, optional):
            whether to apply OCR asynchronously.
            Defaults to False.
        - create_document (bool, optional): whether to
        create or update a document. Defaults to True.

        Returns:
            Document: the created or updated document
        """
        print('yoyo'*50)
        print('parent::::::::::::::', parent)
        if parent is None:
            user, lang, inbox = self.get_user_properties(user)
            print(user, '()()()()',lang, '()()()()', inbox)
            # in case of upload via REST API, LOCAL, or IMAP interface,
            # documents must land in user's inbox
            print('self.processor', self.processor)
            if self.processor in (REST_API, LOCAL, IMAP):
                parent = inbox.id
                print('parent', parent)
                
        print("nameeeeeeeeeeeooooooooo", name)
        
        if name is None:
            name = basename(self.path)
            print("nameeeeeeeeeee", name)
        page_count = self.page_count()
        size = getsize(self.path)
        print("sizeeeeeeeeeeeee", size)
        print('create_document', create_document)

        if create_document and self.doc is None:
            try:
                print('create'*25)
                doc = Document.objects.create_document(
                    user=user,
                    title=name,
                    size=size,
                    lang=lang,
                    file_name=name,
                    parent_id=parent,
                    page_count=page_count,
                    notes=notes
                )
                print('+++++XXXXXXXXX', name, '333', notes, '333', parent, 'page_count', page_count)
                self.doc = doc
            except ValidationError as error:
                print('errorrrrrrrrrrrrrrrr', error)
                logger.error(f"{self.processor} importer: validation failed")
                raise error
        elif self.doc is not None:
            print('doc'*25)
            doc = self.doc
            doc.version = doc.version + 1
            doc.page_count = page_count
            doc.file_name = name
            doc.size = size
            doc.save()
            try:
                print('doc.recreate_pages()')
                doc.recreate_pages()
            except ValueError as e:
                print('createeeeeeeeeeeee', e)
                doc.create_pages()
            doc.full_clean()
        print('BBBBBBBBBBBRRRRRRRRRR')
        self.move_tempfile(doc)
        print('delete temp file')
        self.payload.close()
        print('skip_ocr', skip_ocr)
        # if not skip_ocr:

        #     namespace = default_storage.upload(
        #         doc_path_url=doc.path().url()
        #     )

        #     if apply_async:
        #         for page_num in range(1, page_count + 1):
        #             ocr_page.apply_async(kwargs={
        #                 'user_id': user.id,
        #                 'document_id': doc.id,
        #                 'file_name': name,
        #                 'page_num': page_num,
        #                 'lang': lang,
        #                 'namespace': namespace
        #             })
        #     else:
        #         self.ocr_document(
        #             document=doc,
        #             page_count=page_count,
        #             lang=lang,
        #         )
        print('complete')
        print('docccccccccc', doc)
        logger.debug(f"{self.processor} importer: import complete.")
        return doc


def go_through_pipelines(init_kwargs, apply_kwargs):
    """
    Method to go through all the loaded pipelines **in order**. The init and
    apply dictionaries are not reset for each pipeline, they are updated with
    the results of get_init_kwargs and get_apply_kwargs. This means arguments
    need to be set to None by pipelines as well.

    Args:
        init_kwargs (dict): initial init_kwargs
        apply_kwargs (dict): initial apply_kwargs

    Returns:
        Document: created document, needs to be unique for each payload
    """
    print('@@@@'*50)
    processor = init_kwargs.get('processor', WEB)
    doc = None
    pipelines = settings.PAPERMERGE_PIPELINES
    logger.info(f"{processor} importer: importing file")
    print('pipeline', pipelines)

    for pipeline in pipelines:

        try:
            pipeline_class = module_loading.import_string(pipeline)
            print('pipeline_class', pipeline_class)
        except ImportError:
            print('error'*50)
            logger.error(
                f"{pipeline} could not be loaded."
                " Check if it is installed properly."
            )
            continue
        try:
            print('init_kwargs>>>>>>', init_kwargs)
            importer = pipeline_class(**init_kwargs)
        except TypeError:
            logger.debug(f"{processor} importer: not a file")
            break
        except FileTypeNotSupported:
            logger.debug(f"{processor} importer: filetype not supported")
            continue

        doc = importer.apply(**apply_kwargs)
        logger.info(f"{processor} importer: payload processed successfully")

        init_kwargs_temp = importer.get_init_kwargs()
        apply_kwargs_temp = importer.get_apply_kwargs()

        if init_kwargs_temp:
            init_kwargs = {**init_kwargs, **init_kwargs_temp}
        if apply_kwargs_temp:
            apply_kwargs = {**apply_kwargs, **apply_kwargs_temp}
    return doc
