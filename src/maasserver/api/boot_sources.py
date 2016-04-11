# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""API handlers: `BootSource`."""

__all__ = [
    'BootSourceHandler',
    'BootSourcesHandler',
    ]

from base64 import b64encode
import http.client

from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from maasserver.api.support import OperationsHandler
from maasserver.exceptions import MAASAPIValidationError
from maasserver.forms import BootSourceForm
from maasserver.models import BootSource
from piston3.emitters import JSONEmitter
from piston3.handler import typemapper
from piston3.utils import rc


DISPLAYED_BOOTSOURCE_FIELDS = (
    'id',
    'url',
    'keyring_filename',
    'keyring_data',
)


def json_boot_source(boot_source, request):
    """Convert boot_source into a json object.

    Use the same fields used when serialising objects, but base64-encode
    keyring_data.
    """
    dict_representation = {
        fieldname: getattr(boot_source, fieldname)
        for fieldname in DISPLAYED_BOOTSOURCE_FIELDS
        }
    # Encode the keyring_data as base64.
    keyring_data = getattr(boot_source, 'keyring_data')
    dict_representation['keyring_data'] = b64encode(keyring_data)
    dict_representation['resource_uri'] = reverse(
        'boot_source_handler',
        args=[boot_source.id])
    emitter = JSONEmitter(dict_representation, typemapper, None)
    stream = emitter.render(request)
    return stream


class BootSourceHandler(OperationsHandler):
    """Manage a boot source."""
    api_doc_section_name = "Boot source"
    create = replace = None

    model = BootSource
    fields = DISPLAYED_BOOTSOURCE_FIELDS

    def read(self, request, id):
        """Read a boot source."""
        boot_source = get_object_or_404(
            BootSource, id=id)
        stream = json_boot_source(boot_source, request)
        return HttpResponse(
            stream, content_type='application/json; charset=utf-8',
            status=int(http.client.OK))

    def update(self, request, id):
        """Update a specific boot source.

        :param url: The URL of the BootSource.
        :param keyring_filename: The path to the keyring file for this
            BootSource.
        :param keyring_filename: The GPG keyring for this BootSource,
            base64-encoded data.
        """
        boot_source = get_object_or_404(
            BootSource, id=id)
        form = BootSourceForm(
            data=request.data, files=request.FILES, instance=boot_source)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    def delete(self, request, id):
        """Delete a specific boot source."""
        boot_source = get_object_or_404(
            BootSource, id=id)
        boot_source.delete()
        return rc.DELETED

    @classmethod
    def resource_uri(cls, bootsource=None):
        if bootsource is None:
            id = 'id'
        else:
            id = bootsource.id
        return ('boot_source_handler', (id, ))


class BootSourcesHandler(OperationsHandler):
    """Manage the collection of boot sources."""
    api_doc_section_name = "Boot sources"

    create = replace = update = delete = None

    @classmethod
    def resource_uri(cls):
        return ('boot_sources_handler', [])

    def read(self, request):
        """List boot sources.

        Get a listing of boot sources.
        """
        return BootSource.objects.all()

    def create(self, request):
        """Create a new boot source.

        :param url: The URL of the BootSource.
        :param keyring_filename: The path to the keyring file for
            this BootSource.
        :param keyring_data: The GPG keyring for this BootSource,
            base64-encoded.
        """
        form = BootSourceForm(
            data=request.data, files=request.FILES)
        if form.is_valid():
            boot_source = form.save()
            stream = json_boot_source(boot_source, request)
            return HttpResponse(
                stream, content_type='application/json; charset=utf-8',
                status=int(http.client.CREATED))
        else:
            raise MAASAPIValidationError(form.errors)
