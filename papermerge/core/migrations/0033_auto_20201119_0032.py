# Generated by Django 3.0.10 on 2020-11-18 23:32

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_auto_20201118_1538'),
    ]

    operations = [
        migrations.AlterField(
            model_name='automate',
            name='dst_folder',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='core.Folder', verbose_name='Destination Folder'),
        ),
    ]
