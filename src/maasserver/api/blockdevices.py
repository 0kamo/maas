# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""API handlers: `BlockDevice`."""

from django.core.exceptions import PermissionDenied
from maasserver.api.support import (
    admin_method,
    operation,
    OperationsHandler,
)
from maasserver.api.utils import get_mandatory_param
from maasserver.enum import (
    NODE_PERMISSION,
    NODE_STATUS,
)
from maasserver.exceptions import (
    MAASAPIBadRequest,
    MAASAPIValidationError,
    NodeStateViolation,
)
from maasserver.forms import (
    CreatePhysicalBlockDeviceForm,
    FormatBlockDeviceForm,
    MountBlockDeviceForm,
    UpdatePhysicalBlockDeviceForm,
    UpdateVirtualBlockDeviceForm,
)
from maasserver.models import (
    BlockDevice,
    Machine,
    PartitionTable,
    PhysicalBlockDevice,
    VirtualBlockDevice,
)
from piston3.utils import rc


DISPLAYED_BLOCKDEVICE_FIELDS = (
    'id',
    'name',
    'uuid',
    'type',
    'path',
    'model',
    'serial',
    'id_path',
    'size',
    'block_size',
    'available_size',
    'used_size',
    'used_for',
    'tags',
    'filesystem',
    'partition_table_type',
    'partitions',
)


def raise_error_for_invalid_state_on_allocated_operations(
        node, user, operation):
    if node.status not in [NODE_STATUS.READY, NODE_STATUS.ALLOCATED]:
        raise NodeStateViolation(
            "Cannot %s block device because the node is not Ready "
            "or Allocated." % operation)
    if node.status == NODE_STATUS.READY and not user.is_superuser:
        raise PermissionDenied(
            "Cannot %s block device because you don't have the "
            "permissions on a Ready node." % operation)


class BlockDevicesHandler(OperationsHandler):
    """Manage block devices on a node."""
    api_doc_section_name = "Block devices"
    replace = update = delete = None
    fields = DISPLAYED_BLOCKDEVICE_FIELDS

    @classmethod
    def resource_uri(cls, *args, **kwargs):
        return ('blockdevices_handler', ["system_id"])

    def read(self, request, system_id):
        """List all block devices belonging to node.

        Returns 404 if the machine is not found.
        """
        machine = Machine.objects.get_node_or_404(
            system_id, request.user, NODE_PERMISSION.VIEW)
        return machine.blockdevice_set.all()

    @admin_method
    def create(self, request, system_id):
        """Create a physical block device.

        :param name: Name of the block device.
        :param model: Model of the block device.
        :param serial: Serial number of the block device.
        :param id_path: (optional) Only used if model and serial cannot be
            provided. This should be a path that is fixed and doesn't change
            depending on the boot order or kernel version.
        :param size: Size of the block device.
        :param block_size: Block size of the block device.

        Returns 404 if the node is not found.
        """
        machine = Machine.objects.get_node_or_404(
            system_id, request.user, NODE_PERMISSION.ADMIN)
        form = CreatePhysicalBlockDeviceForm(machine, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)


class BlockDeviceHandler(OperationsHandler):
    """Manage a block device on a node."""
    api_doc_section_name = "Block device"
    create = replace = None
    model = BlockDevice
    fields = DISPLAYED_BLOCKDEVICE_FIELDS

    @classmethod
    def resource_uri(cls, block_device=None):
        # See the comment in NodeHandler.resource_uri.
        if block_device is None:
            system_id = "system_id"
            device_id = "device_id"
        else:
            device_id = block_device.id
            system_id = block_device.node.system_id
        return ('blockdevice_handler', (system_id, device_id))

    @classmethod
    def name(cls, block_device):
        return block_device.actual_instance.get_name()

    @classmethod
    def uuid(cls, block_device):
        block_device = block_device.actual_instance
        if isinstance(block_device, VirtualBlockDevice):
            return block_device.uuid
        else:
            return None

    @classmethod
    def _model(cls, block_device):
        """Return the model for the block device.

        This is method is named `_model` because to Piston model is reserved
        because of the model attribute on the handler.
        See `maasserver.api.support.method_fields_reserved_fields_patch" for
        how this is handled.
        """
        block_device = block_device.actual_instance
        if isinstance(block_device, PhysicalBlockDevice):
            return block_device.model
        else:
            return None

    @classmethod
    def serial(cls, block_device):
        block_device = block_device.actual_instance
        if isinstance(block_device, PhysicalBlockDevice):
            return block_device.serial
        else:
            return None

    @classmethod
    def filesystem(cls, block_device):
        # XXX: This is almost the same as
        # m.api.partitions.PartitionHandler.filesystem.
        block_device = block_device.actual_instance
        filesystem = block_device.get_effective_filesystem()
        if filesystem is not None:
            return {
                'fstype': filesystem.fstype,
                'label': filesystem.label,
                'uuid': filesystem.uuid,
                'mount_point': filesystem.mount_point,
                'mount_params': filesystem.mount_params,
            }
        else:
            return None

    @classmethod
    def partition_table_type(cls, block_device):
        try:
            partition_table = block_device.partitiontable_set.get()
        except PartitionTable.DoesNotExist:
            # No partition table on the block device.
            return None
        return partition_table.table_type

    @classmethod
    def partitions(cls, block_device):
        try:
            partition_table = block_device.partitiontable_set.get()
        except PartitionTable.DoesNotExist:
            # No partitions on the block device.
            return []
        return partition_table.partitions.all()

    def read(self, request, system_id, device_id):
        """Read block device on node.

        Returns 404 if the node or block device is not found.
        """
        return BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.VIEW)

    def delete(self, request, system_id, device_id):
        """Delete block device on node.

        Returns 404 if the node or block device is not found.
        Returns 403 if the user is not allowed to delete the block device.
        Returns 409 if the node is not Ready.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.ADMIN)
        node = device.get_node()
        if node.status != NODE_STATUS.READY:
            raise NodeStateViolation(
                "Cannot delete block device because the node is not Ready.")
        device.delete()
        return rc.DELETED

    def update(self, request, system_id, device_id):
        """Update block device on node.

        Fields for physical block device:
        :param name: Name of the block device.
        :param model: Model of the block device.
        :param serial: Serial number of the block device.
        :param id_path: (optional) Only used if model and serial cannot be \
            provided. This should be a path that is fixed and doesn't change \
            depending on the boot order or kernel version.
        :param size: Size of the block device.
        :param block_size: Block size of the block device.

        Fields for virtual block device:
        :param name: Name of the block device.
        :param uuid: UUID of the block device.
        :param size: Size of the block device. (Only allowed for logical \
            volumes.)

        Returns 404 if the node or block device is not found.
        Returns 403 if the user is not allowed to update the block device.
        Returns 409 if the node is not Ready.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.ADMIN)
        node = device.get_node()
        if node.status != NODE_STATUS.READY:
            raise NodeStateViolation(
                "Cannot update block device because the node is not Ready.")
        if device.type == 'physical':
            form = UpdatePhysicalBlockDeviceForm(
                instance=device, data=request.data)
        elif device.type == 'virtual':
            form = UpdateVirtualBlockDeviceForm(
                instance=device, data=request.data)
        else:
            raise ValueError(
                'Cannot update block device of type %s' % device.type)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    @operation(idempotent=True)
    def add_tag(self, request, system_id, device_id):
        """Add a tag to block device on node.

        :param tag: The tag being added.

        Returns 404 if the node or block device is not found.
        Returns 403 if the user is not allowed to update the block device.
        Returns 409 if the node is not Ready.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.ADMIN)
        node = device.get_node()
        if node.status != NODE_STATUS.READY:
            raise NodeStateViolation(
                "Cannot update block device because the node is not Ready.")
        device.add_tag(get_mandatory_param(request.GET, 'tag'))
        device.save()
        return device

    @operation(idempotent=True)
    def remove_tag(self, request, system_id, device_id):
        """Remove a tag from block device on node.

        :param tag: The tag being removed.

        Returns 404 if the node or block device is not found.
        Returns 403 if the user is not allowed to update the block device.
        Returns 409 if the node is not Ready.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.ADMIN)
        node = device.get_node()
        if node.status != NODE_STATUS.READY:
            raise NodeStateViolation(
                "Cannot update block device because the node is not Ready.")
        device.remove_tag(get_mandatory_param(request.GET, 'tag'))
        device.save()
        return device

    @operation(idempotent=False)
    def format(self, request, system_id, device_id):
        """Format block device with filesystem.

        :param fstype: Type of filesystem.
        :param uuid: UUID of the filesystem.

        Returns 403 when the user doesn't have the ability to format the \
            block device.
        Returns 404 if the node or block device is not found.
        Returns 409 if the node is not Ready or Allocated.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.EDIT)
        node = device.get_node()
        raise_error_for_invalid_state_on_allocated_operations(
            node, request.user, "format")
        form = FormatBlockDeviceForm(device, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    @operation(idempotent=False)
    def unformat(self, request, system_id, device_id):
        """Unformat block device with filesystem.

        Returns 400 if the block device is not formatted, currently mounted, \
            or part of a filesystem group.
        Returns 403 when the user doesn't have the ability to unformat the \
            block device.
        Returns 404 if the node or block device is not found.
        Returns 409 if the node is not Ready or Allocated.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.EDIT)
        node = device.get_node()
        raise_error_for_invalid_state_on_allocated_operations(
            node, request.user, "unformat")
        filesystem = device.get_effective_filesystem()
        if filesystem is None:
            raise MAASAPIBadRequest("Block device is not formatted.")
        if filesystem.mount_point:
            raise MAASAPIBadRequest(
                "Filesystem is mounted and cannot be unformatted. Unmount the "
                "filesystem before unformatting the block device.")
        if filesystem.filesystem_group is not None:
            nice_name = filesystem.filesystem_group.get_nice_name()
            raise MAASAPIBadRequest(
                "Filesystem is part of a %s, and cannot be "
                "unformatted. Remove block device from %s "
                "before unformatting the block device." % (
                    nice_name, nice_name))
        filesystem.delete()
        return device

    @operation(idempotent=False)
    def mount(self, request, system_id, device_id):
        """Mount the filesystem on block device.

        :param mount_point: Path on the filesystem to mount.

        Returns 403 when the user doesn't have the ability to mount the \
            block device.
        Returns 404 if the node or block device is not found.
        Returns 409 if the node is not Ready or Allocated.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.EDIT)
        node = device.get_node()
        raise_error_for_invalid_state_on_allocated_operations(
            node, request.user, "mount")
        form = MountBlockDeviceForm(device, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    @operation(idempotent=False)
    def unmount(self, request, system_id, device_id):
        """Unmount the filesystem on block device.

        Returns 400 if the block device is not formatted or not currently \
            mounted.
        Returns 403 when the user doesn't have the ability to unmount the \
            block device.
        Returns 404 if the node or block device is not found.
        Returns 409 if the node is not Ready or Allocated.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.EDIT)
        node = device.get_node()
        raise_error_for_invalid_state_on_allocated_operations(
            node, request.user, "unmount")
        filesystem = device.get_effective_filesystem()
        if filesystem is None:
            raise MAASAPIBadRequest("Block device is not formatted.")
        if not filesystem.mount_point:
            raise MAASAPIBadRequest("Filesystem is already unmounted.")
        filesystem.mount_point = None
        filesystem.mount_params = None
        filesystem.save()
        return device

    @operation(idempotent=False)
    def set_boot_disk(self, request, system_id, device_id):
        """Set this block device as the boot disk for the node.

        Returns 400 if the block device is a virtual block device.
        Returns 404 if the node or block device is not found.
        Returns 403 if the user is not allowed to update the block device.
        Returns 409 if the node is not Ready or Allocated.
        """
        device = BlockDevice.objects.get_block_device_or_404(
            system_id, device_id, request.user, NODE_PERMISSION.ADMIN)
        node = device.get_node()
        if node.status != NODE_STATUS.READY:
            raise NodeStateViolation(
                "Cannot set as boot disk because the node is not Ready.")
        if not isinstance(device, PhysicalBlockDevice):
            raise MAASAPIBadRequest(
                "Cannot set a %s block device as the boot disk." % device.type)
        device.node.boot_disk = device
        device.node.save()
        return rc.ALL_OK


class PhysicalBlockDeviceHandler(BlockDeviceHandler):
    """
    This handler only exists because piston requires a unique handler per
    class type. Without this class the resource_uri will not be added to any
    object that is of type `PhysicalBlockDevice` when it is emitted from the
    `BlockDeviceHandler`.

    Important: This should not be used in the urls_api.py. This is only here
        to support piston.
    """
    hidden = True
    model = PhysicalBlockDevice


class VirtualBlockDeviceHandler(BlockDeviceHandler):
    """
    This handler only exists because piston requires a unique handler per
    class type. Without this class the resource_uri will not be added to any
    object that is of type `VirtualBlockDevice` when it is emitted from the
    `BlockDeviceHandler`.

    Important: This should not be used in the urls_api.py. This is only here
        to support piston.
    """
    hidden = True
    model = VirtualBlockDevice
