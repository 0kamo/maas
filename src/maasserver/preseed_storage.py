# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Preseed generation for curtin storage."""

__all__ = [
    "compose_curtin_storage_config",
]

from operator import attrgetter

from maasserver.enum import (
    FILESYSTEM_GROUP_TYPE,
    FILESYSTEM_TYPE,
    PARTITION_TABLE_TYPE,
)
from maasserver.models.partitiontable import (
    INITIAL_PARTITION_OFFSET,
    PREP_PARTITION_SIZE,
)
from maasserver.models.physicalblockdevice import PhysicalBlockDevice
from maasserver.models.virtualblockdevice import VirtualBlockDevice
import yaml


class CurtinStorageGenerator:
    """Generates the YAML storage configuration for curtin."""

    def __init__(self, node):
        self.node = node
        self.boot_disk = node.get_boot_disk()
        self.boot_disk_first_partition = None
        self.operations = {
            "disk": [],
            "partition": [],
            "format": [],
            "mount": [],
            "lvm_volgroup": [],
            "lvm_partition": [],
            "raid": [],
            "bcache": [],
        }

    def generate(self):
        """Create the YAML storage configuration for curtin."""
        self.storage_config = []

        # Add all the items to operations.
        self._add_disk_and_filesystem_group_operations()
        self._add_partition_operations()
        self._add_format_and_mount_operations()

        # Generate each YAML operation in the storage_config.
        self._generate_disk_operations()
        self._generate_volume_group_operations()
        self._generate_logical_volume_operations()
        self._generate_raid_operations()
        self._generate_bcache_operations()
        self._generate_partition_operations()
        self._generate_format_operations()

        # Order the storage_config where dependencies come first.
        self._order_config_dependency()

        # Generate the mount operations that go at the end of the
        # storage_config.
        self._generate_mount_operations()

        # Render the resulting YAML.
        storage_config = {
            "partitioning_commands": {
                "builtin": ["curtin", "block-meta", "custom"],
            },
            "storage": {
                "version": 1,
                "config": self.storage_config,
            },
        }
        return yaml.safe_dump(storage_config)

    def _add_disk_and_filesystem_group_operations(self):
        """Add all disk and filesystem group (lvm, raid, bcache) operations.

        These operations come from all of the physical block devices attached
        to the node.
        """
        for block_device in self.node.blockdevice_set.order_by('id'):
            block_device = block_device.actual_instance
            if isinstance(block_device, PhysicalBlockDevice):
                self.operations["disk"].append(block_device)
            elif isinstance(block_device, VirtualBlockDevice):
                filesystem_group = block_device.filesystem_group
                if filesystem_group.is_lvm():
                    if filesystem_group not in self.operations["lvm_volgroup"]:
                        self.operations["lvm_volgroup"].append(
                            filesystem_group)
                    self.operations["lvm_partition"].append(block_device)
                elif filesystem_group.is_raid():
                    self.operations["raid"].append(filesystem_group)
                elif filesystem_group.is_bcache():
                    self.operations["bcache"].append(filesystem_group)
                else:
                    raise ValueError(
                        "Unknown filesystem group type: %s" % (
                            filesystem_group.group_type))
            else:
                raise ValueError("Unknown block device instance: %s" % (
                    block_device.__class__.__name__))

    def _requires_prep_partition(self, block_device):
        """Return True if block device requires the prep partition."""
        arch, _ = self.node.split_arch()
        return (
            self.boot_disk.id == block_device.id and
            arch == "ppc64el")

    def _add_partition_operations(self):
        """Add all the partition operations.

        These operations come from all the partitions on all block devices
        attached to the node.
        """
        for block_device in self.node.blockdevice_set.order_by('id'):
            requires_prep = self._requires_prep_partition(block_device)
            partition_table = block_device.get_partitiontable()
            if partition_table is not None:
                partitions = list(partition_table.partitions.order_by('id'))
                for idx, partition in enumerate(partitions):
                    # If this is the last partition and prep partition is
                    # required then set boot_disk_first_partition so extra
                    # space can be removed.
                    if requires_prep and idx == 0:
                        self.boot_disk_first_partition = partition
                    self.operations["partition"].append(partition)

    def _add_format_and_mount_operations(self):
        """Add all the format and mount operations.

        These operations come from all the block devices and partitions
        attached to the node.
        """
        for block_device in self.node.blockdevice_set.order_by('id'):
            filesystem = block_device.get_effective_filesystem()
            if self._requires_format_operation(filesystem):
                self.operations["format"].append(filesystem)
                if filesystem.is_mounted:
                    self.operations["mount"].append(filesystem)
            else:
                partition_table = block_device.get_partitiontable()
                if partition_table is not None:
                    for partition in partition_table.partitions.order_by('id'):
                        partition_filesystem = (
                            partition.get_effective_filesystem())
                        if self._requires_format_operation(
                                partition_filesystem):
                            self.operations["format"].append(
                                partition_filesystem)
                            if partition_filesystem.is_mounted:
                                self.operations["mount"].append(
                                    partition_filesystem)

    def _requires_format_operation(self, filesystem):
        """Return True if the filesystem requires a format operation."""
        return (
            filesystem is not None and
            filesystem.filesystem_group_id is None and
            filesystem.cache_set is None)

    def _generate_disk_operations(self):
        """Generate all disk operations."""
        for block_device in self.operations["disk"]:
            self._generate_disk_operation(block_device)

    def _generate_disk_operation(self, block_device):
        """Generate disk operation for `block_device` and place in
        `storage_config`."""
        disk_operation = {
            "id": block_device.get_name(),
            "name": block_device.get_name(),
            "type": "disk",
            "wipe": "superblock",
        }
        # Set model and serial unless not set, then curtin will use a device
        # path to match.
        if block_device.model and block_device.serial:
            disk_operation["model"] = block_device.model
            disk_operation["serial"] = block_device.serial
        else:
            disk_operation["path"] = block_device.id_path

        # Set the partition table type if a partition table exists or if this
        # is the boot disk.
        add_prep_partition = False
        partition_table = block_device.get_partitiontable()
        if partition_table is not None:
            disk_operation["ptable"] = self._get_ptable_type(
                partition_table)
        elif block_device.id == self.boot_disk.id:
            bios_boot_method = self.node.get_bios_boot_method()
            node_arch, _ = self.node.split_arch()
            if bios_boot_method in [
                    "uefi", "powernv", "powerkvm"]:
                disk_operation["ptable"] = "gpt"
                if node_arch == "ppc64el":
                    add_prep_partition = True
            else:
                disk_operation["ptable"] = "msdos"

        # Set this disk to be the grub device if it's the boot disk and doesn't
        # require a prep partition. When a prep partition is required grub
        # must be installed on that partition and not in the partition header
        # of that disk.
        requires_prep = self._requires_prep_partition(block_device)
        if self.boot_disk.id == block_device.id and not requires_prep:
            disk_operation["grub_device"] = True
        self.storage_config.append(disk_operation)

        # Add the prep partition at the end of the disk when it is required.
        if add_prep_partition:
            self._generate_prep_partition(block_device.get_name())

    def _get_ptable_type(self, partition_table):
        """Return the value for the "ptable" entry in the physical operation.
        """
        if partition_table.table_type == PARTITION_TABLE_TYPE.MBR:
            return "msdos"
        elif partition_table.table_type == PARTITION_TABLE_TYPE.GPT:
            return "gpt"
        else:
            raise ValueError(
                "Unknown partition table type: %s" % (
                    partition_table.table_type))

    def _generate_prep_partition(self, device_name):
        """Generate the prep partition at the beginning of the block device."""
        prep_part_name = "%s-part1" % (device_name)
        partition_operation = {
            "id": prep_part_name,
            "name": prep_part_name,
            "type": "partition",
            "number": 1,
            "offset": "%dB" % INITIAL_PARTITION_OFFSET,
            "size": "%dB" % PREP_PARTITION_SIZE,
            "device": device_name,
            "wipe": "zero",
            "flag": "prep",
            "grub_device": True,
        }
        self.storage_config.append(partition_operation)

    def _generate_partition_operations(self):
        """Generate all partition operations."""
        for partition in self.operations["partition"]:
            if partition == self.boot_disk_first_partition:
                # This is the first partition in the boot disk and add prep
                # partition at the beginning of the partition table.
                device_name = partition.partition_table.block_device.get_name()
                self._generate_prep_partition(device_name)
                self._generate_partition_operation(
                    partition, include_initial=False)
            else:
                self._generate_partition_operation(
                    partition, include_initial=True)

    def _generate_partition_operation(self, partition, include_initial):
        """Generate partition operation for `partition` and place in
        `storage_config`."""
        partition_table = partition.partition_table
        block_device = partition_table.block_device
        partition_number = partition.get_partition_number()
        partition_operation = {
            "id": partition.get_name(),
            "name": partition.get_name(),
            "type": "partition",
            "number": partition_number,
            "uuid": partition.uuid,
            "size": "%sB" % partition.size,
            "device": block_device.get_name(),
            "wipe": "superblock",
        }
        # First partition always sets the initial offset.
        if partition_number == 1 and include_initial:
            partition_operation["offset"] = "%sB" % INITIAL_PARTITION_OFFSET
        if partition.bootable:
            partition_operation["flag"] = "boot"
        if partition_table.table_type == PARTITION_TABLE_TYPE.MBR:
            # Fifth partition on an MBR partition, must add the extend
            # partition operation. So the remaining partitions can be added.
            if partition_number == 5:
                self.storage_config.append({
                    "id": "%s-part4" % block_device.get_name(),
                    "type": "partition",
                    "number": 4,
                    "device": block_device.get_name(),
                    "flag": "extended",
                })
                partition_operation["flag"] = "logical"
            elif partition_number > 5:
                partition_operation["flag"] = "logical"
        self.storage_config.append(partition_operation)

    def _generate_format_operations(self):
        """Generate all format operations."""
        for filesystem in self.operations["format"]:
            self._generate_format_operation(filesystem)

    def _generate_format_operation(self, filesystem):
        """Generate format operation for `filesystem` and place in
        `storage_config`."""
        device_or_partition = filesystem.get_parent()
        self.storage_config.append({
            "id": "%s_format" % device_or_partition.get_name(),
            "type": "format",
            "fstype": filesystem.fstype,
            "uuid": filesystem.uuid,
            "label": filesystem.label,
            "volume": device_or_partition.get_name(),
        })

    def _generate_volume_group_operations(self):
        """Generate all volume group operations."""
        for filesystem_group in self.operations["lvm_volgroup"]:
            self._generate_volume_group_operation(filesystem_group)

    def _generate_volume_group_operation(self, filesystem_group):
        """Generate volume group operation for `filesystem_group` and place in
        `storage_config`."""
        volume_group_operation = {
            "id": filesystem_group.name,
            "name": filesystem_group.name,
            "type": "lvm_volgroup",
            "uuid": filesystem_group.uuid,
            "devices": [],
        }
        for filesystem in filesystem_group.filesystems.all():
            block_or_partition = filesystem.get_parent()
            volume_group_operation["devices"].append(
                block_or_partition.get_name())
        volume_group_operation["devices"] = sorted(
            volume_group_operation["devices"])
        self.storage_config.append(volume_group_operation)

    def _generate_logical_volume_operations(self):
        """Generate all logical volume operations."""
        for block_device in self.operations["lvm_partition"]:
            self._generate_logical_volume_operation(block_device)

    def _generate_logical_volume_operation(self, block_device):
        """Generate logical volume operation for `block_device` and place in
        `storage_config`."""
        self.storage_config.append({
            "id": block_device.get_name(),
            "name": block_device.name,  # Use name of logical volume only.
            "type": "lvm_partition",
            "volgroup": block_device.filesystem_group.name,
            "size": "%sB" % block_device.size,
        })

    def _generate_raid_operations(self):
        """Generate all raid operations."""
        for filesystem_group in self.operations["raid"]:
            self._generate_raid_operation(filesystem_group)

    def _generate_raid_operation(self, filesystem_group):
        """Generate raid operation for `filesystem_group` and place in
        `storage_config`."""
        raid_operation = {
            "id": filesystem_group.name,
            "name": filesystem_group.name,
            "type": "raid",
            "raidlevel": self._get_raid_level(filesystem_group),
            "devices": [],
            "spare_devices": [],
        }
        for filesystem in filesystem_group.filesystems.all():
            block_or_partition = filesystem.get_parent()
            if filesystem.fstype == FILESYSTEM_TYPE.RAID:
                raid_operation["devices"].append(
                    block_or_partition.get_name())
            elif filesystem.fstype == FILESYSTEM_TYPE.RAID_SPARE:
                raid_operation["spare_devices"].append(
                    block_or_partition.get_name())
        raid_operation["devices"] = sorted(raid_operation["devices"])
        raid_operation["spare_devices"] = sorted(
            raid_operation["spare_devices"])
        block_device = filesystem_group.virtual_device
        partition_table = block_device.get_partitiontable()
        if partition_table is not None:
            raid_operation["ptable"] = self._get_ptable_type(partition_table)
        self.storage_config.append(raid_operation)

    def _get_raid_level(self, filesystem_group):
        """Return the raid level for the filesystem group type."""
        raid_levels = {
            FILESYSTEM_GROUP_TYPE.RAID_0: 0,
            FILESYSTEM_GROUP_TYPE.RAID_1: 1,
            FILESYSTEM_GROUP_TYPE.RAID_5: 5,
            FILESYSTEM_GROUP_TYPE.RAID_6: 6,
            FILESYSTEM_GROUP_TYPE.RAID_10: 10,
        }
        return raid_levels[filesystem_group.group_type]

    def _generate_bcache_operations(self):
        """Generate all bcache operations."""
        for filesystem_group in self.operations["bcache"]:
            self._generate_bcache_operation(filesystem_group)

    def _generate_bcache_operation(self, filesystem_group):
        """Generate bcache operation for `filesystem_group` and place in
        `storage_config`."""
        bcache_operation = {
            "id": filesystem_group.name,
            "name": filesystem_group.name,
            "type": "bcache",
            "backing_device": filesystem_group.get_bcache_backing_filesystem(
                ).get_parent().get_name(),
            "cache_device": filesystem_group.cache_set.get_device().get_name(),
            "cache_mode": filesystem_group.cache_mode,
        }
        block_device = filesystem_group.virtual_device
        partition_table = block_device.get_partitiontable()
        if partition_table is not None:
            bcache_operation["ptable"] = self._get_ptable_type(partition_table)
        self.storage_config.append(bcache_operation)

    def _order_config_dependency(self):
        """Re-order the storage config so dependencies appear before
        dependents."""
        # Continuously loop through the storage configuration until a complete
        # pass is made without having to reorder dependencies.
        while True:
            ids_above = []
            for operation in list(self.storage_config):
                operation_type = operation["type"]
                if operation_type == "disk":
                    # Doesn't depend on anything.
                    pass
                elif operation_type == "partition":
                    device = operation["device"]
                    if device not in ids_above:
                        self._reorder_operation(operation, device)
                        break
                elif operation_type == "format":
                    volume = operation["volume"]
                    if volume not in ids_above:
                        self._reorder_operation(operation, volume)
                        break
                elif operation_type == "lvm_volgroup":
                    exit_early = False
                    for device in operation["devices"]:
                        if device not in ids_above:
                            self._reorder_operation(operation, device)
                            exit_early = True
                            break
                    if exit_early:
                        break
                elif operation_type == "lvm_partition":
                    volgroup = operation["volgroup"]
                    if volgroup not in ids_above:
                        self._reorder_operation(operation, volgroup)
                        break
                elif operation_type == "raid":
                    exit_early = False
                    for device in operation["devices"]:
                        if device not in ids_above:
                            self._reorder_operation(operation, device)
                            exit_early = True
                            break
                    if exit_early:
                        break
                    for device in operation["spare_devices"]:
                        if device not in ids_above:
                            self._reorder_operation(operation, device)
                            exit_early = True
                            break
                    if exit_early:
                        break
                elif operation_type == "bcache":
                    backing_device = operation["backing_device"]
                    if backing_device not in ids_above:
                        self._reorder_operation(operation, backing_device)
                        break
                    cache_device = operation["cache_device"]
                    if cache_device not in ids_above:
                        self._reorder_operation(operation, cache_device)
                        break
                else:
                    raise ValueError(
                        "Unknown operation type: %s" % operation_type)
                ids_above.append(operation["id"])

            # If parsed the entire storage config without breaking out of the
            # loop then all dependencies are in order.
            if len(ids_above) == len(self.storage_config):
                break

    def _reorder_operation(self, operation, dependent_id):
        """Reorder the `operation` to be after `dependent_id` in the
        `storage_config`."""
        # Remove the operation from the storage_config.
        self.storage_config.remove(operation)

        # Place the operation after the dependent in the storage_config.
        dependent_idx = [
            idx
            for idx, op in enumerate(self.storage_config)
            if op['id'] == dependent_id
        ][0]
        self.storage_config.insert(dependent_idx + 1, operation)

    def _generate_mount_operations(self):
        """Generate all mount operations."""
        # Sort the mounts lexically. This will ensure that the mount
        # operations are performed in a sane order. Without this curtin will
        # mount the filesystems out of order preventing installation from
        # working correctly.
        mount_operations = sorted(
            self.operations["mount"], key=attrgetter("mount_point"))
        for filesystem in mount_operations:
            self._generate_mount_operation(filesystem)

    def _generate_mount_operation(self, filesystem):
        """Generate mount operation for `filesystem` and place in
        `storage_config`."""
        device_or_partition = filesystem.get_parent()
        stanza = {
            "id": "%s_mount" % device_or_partition.get_name(),
            "type": "mount",
            "device": "%s_format" % device_or_partition.get_name(),
        }
        if filesystem.uses_mount_point:
            stanza["path"] = filesystem.mount_point
        if filesystem.mount_options is not None:
            stanza["options"] = filesystem.mount_options
        self.storage_config.append(stanza)


def compose_curtin_storage_config(node):
    """Compose the storage configuration for curtin."""
    generator = CurtinStorageGenerator(node)
    return [generator.generate()]
