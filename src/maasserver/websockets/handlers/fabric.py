# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""The fabric handler for the WebSocket connection."""

__all__ = [
    "FabricHandler",
    ]

from maasserver.enum import NODE_PERMISSION
from maasserver.models.fabric import Fabric
from maasserver.websockets.handlers.timestampedmodel import (
    TimestampedModelHandler,
)


class FabricHandler(TimestampedModelHandler):

    class Meta:
        queryset = (
            Fabric.objects.all().prefetch_related(
                "vlan_set__interface_set"))
        pk = 'id'
        allowed_methods = ['list', 'get', 'create', 'delete', 'set_active']
        listen_channels = [
            "fabric",
            ]

    def dehydrate(self, obj, data, for_list=False):
        data["name"] = obj.get_name()
        data["vlan_ids"] = [
            vlan.id
            for vlan in obj.vlan_set.all()
        ]
        return data

    def delete(self, parameters):
        """Delete this Domain."""
        domain = self.get_object(parameters)
        assert self.user.has_perm(
            NODE_PERMISSION.ADMIN, domain), "Permission denied."
        domain.delete()
