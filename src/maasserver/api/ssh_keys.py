# Copyright 2014-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""API handlers: `SSHKey`."""

__all__ = [
    'SSHKeyHandler',
    'SSHKeysHandler',
    ]

import http.client

from django.conf import settings
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
)
from django.shortcuts import get_object_or_404
from maasserver.api.support import (
    operation,
    OperationsHandler,
)
from maasserver.exceptions import MAASAPIValidationError
from maasserver.forms import SSHKeyForm
from maasserver.models import SSHKey
from piston3.emitters import JSONEmitter
from piston3.handler import typemapper
from piston3.utils import rc


DISPLAY_SSHKEY_FIELDS = ("id", "key")


class SSHKeysHandler(OperationsHandler):
    """Manage the collection of all the SSH keys in this MAAS."""
    api_doc_section_name = "SSH Keys"

    update = delete = None

    @operation(idempotent=True)
    def read(self, request):
        """List all keys belonging to the requesting user."""
        return SSHKey.objects.filter(user=request.user)

    @operation(idempotent=False)
    def create(self, request):
        """Add a new SSH key to the requesting user's account.

        The request payload should contain the public SSH key data in form
        data whose name is "key".
        """
        form = SSHKeyForm(user=request.user, data=request.data)
        if form.is_valid():
            sshkey = form.save()
            emitter = JSONEmitter(
                sshkey, typemapper, None, DISPLAY_SSHKEY_FIELDS)
            stream = emitter.render(request)
            return HttpResponse(
                stream, content_type='application/json; charset=utf-8',
                status=int(http.client.CREATED))
        else:
            raise MAASAPIValidationError(form.errors)

    @classmethod
    def resource_uri(cls, *args, **kwargs):
        return ('sshkeys_handler', [])


class SSHKeyHandler(OperationsHandler):
    """Manage an SSH key.

    SSH keys can be retrieved or deleted.
    """
    api_doc_section_name = "SSH Key"

    fields = DISPLAY_SSHKEY_FIELDS
    model = SSHKey
    create = update = None

    def read(self, request, keyid):
        """GET an SSH key.

        Returns 404 if the key does not exist.
        """
        key = get_object_or_404(SSHKey, id=keyid)
        return key

    @operation(idempotent=False)
    def delete(self, request, keyid):
        """DELETE an SSH key.

        Returns 404 if the key does not exist.
        Returns 401 if the key does not belong to the calling user.
        """
        key = get_object_or_404(SSHKey, id=keyid)
        if key.user != request.user:
            return HttpResponseForbidden(
                "Can't delete a key you don't own.",
                content_type=(
                    "text/plain; charset=%s" % settings.DEFAULT_CHARSET)
            )
        key.delete()
        return rc.DELETED

    @classmethod
    def resource_uri(cls, sshkey=None):
        keyid = "keyid"
        if sshkey is not None:
            keyid = sshkey.id
        return ('sshkey_handler', (keyid, ))
