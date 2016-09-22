# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""The SSHKey handler for the WebSocket connection."""

__all__ = [
    "SSHKeyHandler",
    ]

from django.core.exceptions import ValidationError
from maasserver.forms import SSHKeyForm
from maasserver.models.keysource import KeySource
from maasserver.models.sshkey import SSHKey
from maasserver.utils.keys import ImportSSHKeysError
from maasserver.websockets.base import (
    HandlerDoesNotExistError,
    HandlerError,
    HandlerValidationError,
)
from maasserver.websockets.handlers.timestampedmodel import (
    TimestampedModelHandler,
)


class SSHKeyHandler(TimestampedModelHandler):

    class Meta:
        queryset = SSHKey.objects.all()
        allowed_methods = [
            'list',
            'get',
            'create',
            'delete',
            'import_keys',
        ]
        listen_channels = [
            "sshkey",
        ]

    def get_queryset(self):
        """Return `QuerySet` for SSH keys owned by `user`."""
        return self._meta.queryset.filter(user=self.user)

    def get_object(self, params):
        """Only allow getting keys owned by the user."""
        obj = super(SSHKeyHandler, self).get_object(params)
        if obj.user != self.user:
            raise HandlerDoesNotExistError(params[self._meta.pk])
        else:
            return obj

    def dehydrate_keysource(self, keysource):
        """Dehydrate the keysource to include protocol and auth_id."""
        if keysource is None:
            return None
        else:
            return {
                "protocol": keysource.protocol,
                "auth_id": keysource.auth_id,
            }

    def dehydrate(self, obj, data, for_list=False):
        """Add display to the SSH key."""
        data["display"] = obj.display_html(70)
        return data

    def create(self, params):
        """Create a SSHKey."""
        form = SSHKeyForm(user=self.user, data=params)
        if form.is_valid():
            try:
                obj = form.save()
            except ValidationError as e:
                try:
                    raise HandlerValidationError(e.message_dict)
                except AttributeError:
                    raise HandlerValidationError({"__all__": e.message})
            return self.full_dehydrate(obj)
        else:
            raise HandlerValidationError(form.errors)

    def import_keys(self, params):
        """Import the requesting user's SSH keys.

        Import SSH keys for a given protocol and authorization ID in
        protocol:auth_id format.
        """
        try:
            KeySource.objects.save_keys_for_user(
                user=self.user,
                protocol=params['protocol'],
                auth_id=params['auth_id'])
        except ImportSSHKeysError as e:
            raise HandlerError(str(e))
