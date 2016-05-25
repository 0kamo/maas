/* Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * MAAS Node Storage Controller
 */


// Filter that is specific to the NodeStorageController. Remove the available
// disks from the list if being used in the availableNew.
angular.module('MAAS').filter('removeAvailableByNew', function() {
    return function(disks, availableNew) {
        if(!angular.isObject(availableNew) || (
            !angular.isObject(availableNew.device) &&
            !angular.isArray(availableNew.devices))) {
            return disks;
        }

        var filtered = [];
        var single = true;
        if(angular.isArray(availableNew.devices)) {
            single = false;
        }
        angular.forEach(disks, function(disk) {
            if(single) {
                if(disk !== availableNew.device) {
                    filtered.push(disk);
                }
            } else {
                var i, found = false;
                for(i = 0; i < availableNew.devices.length; i++) {
                    if(disk === availableNew.devices[i]) {
                        found = true;
                        break;
                    }
                }
                if(!found) {
                    filtered.push(disk);
                }
            }
        });
        return filtered;
    };
});

angular.module('MAAS').controller('NodeStorageController', [
    '$scope', 'MachinesManager', 'ConverterService', 'UsersManager',
    function($scope, MachinesManager, ConverterService, UsersManager) {

        // From models/partitiontable.py - must be kept in sync.
        var INITIAL_PARTITION_OFFSET = 4 * 1024 * 1024;
        var END_OF_PARTITION_TABLE_SPACE = 1024 * 1024;
        var PARTITION_TABLE_EXTRA_SPACE = INITIAL_PARTITION_OFFSET +
            END_OF_PARTITION_TABLE_SPACE;
        var PREP_PARTITION_SIZE = 8 * 1024 * 1024;

        // From models/partition.py - must be kept in sync.
        var PARTITION_ALIGNMENT_SIZE = 4 * 1024 * 1024;
        var MIN_PARTITION_SIZE = PARTITION_ALIGNMENT_SIZE;

        // Different selection modes.
        var SELECTION_MODE = {
            NONE: null,
            SINGLE: "single",
            MUTLI: "multi",
            UNMOUNT: "unmount",
            UNFORMAT: "unformat",
            DELETE: "delete",
            FORMAT_AND_MOUNT: "format-mount",
            PARTITION: "partition",
            BCACHE: "bcache",
            RAID: "raid",
            VOLUME_GROUP: "volume-group",
            LOGICAL_VOLUME: "logical-volume"
        };

        // Different available raid modes.
        var RAID_MODES = [
            {
                level: "raid-0",
                title: "RAID 0",
                min_disks: 2,
                allows_spares: false,
                calculateSize: function(minSize, numDisks) {
                    return minSize * numDisks;
                }
            },
            {
                level: "raid-1",
                title: "RAID 1",
                min_disks: 2,
                allows_spares: true,
                calculateSize: function(minSize, numDisks) {
                    return minSize;
                }
            },
            {
                level: "raid-5",
                title: "RAID 5",
                min_disks: 3,
                allows_spares: true,
                calculateSize: function(minSize, numDisks) {
                    return minSize * (numDisks - 1);
                }
            },
            {
                level: "raid-6",
                title: "RAID 6",
                min_disks: 4,
                allows_spares: true,
                calculateSize: function(minSize, numDisks) {
                    return minSize * (numDisks - 2);
                }
            },
            {
                level: "raid-10",
                title: "RAID 10",
                min_disks: 3,
                allows_spares: true,
                calculateSize: function(minSize, numDisks) {
                    return minSize * numDisks / 2;
                }
            }
        ];

        $scope.column = 'name';
        $scope.has_disks = false;
        $scope.filesystems = [];
        $scope.filesystemsMap = {};
        $scope.filesystemMode = SELECTION_MODE.NONE;
        $scope.filesystemAllSelected = false;
        $scope.cachesets = [];
        $scope.cachesetsMap = {};
        $scope.cachesetsMode = SELECTION_MODE.NONE;
        $scope.cachesetsAllSelected = false;
        $scope.available = [];
        $scope.availableMap = {};
        $scope.availableMode = SELECTION_MODE.NONE;
        $scope.availableAllSelected = false;
        $scope.availableNew = {};
        $scope.used = [];

        // Give $parent which is the NodeDetailsController access to this scope
        // it will call `nodeLoaded` once the node has been fully loaded.
        $scope.$parent.storageController = $scope;

        // Return True if the filesystem is mounted.
        function isMountedFilesystem(filesystem) {
            return angular.isObject(filesystem) &&
                angular.isString(filesystem.mount_point) &&
                filesystem.mount_point !== "";
        }

        // Return True if the item has a filesystem and it's mounted.
        function hasMountedFilesystem(item) {
            return angular.isObject(item) &&
                isMountedFilesystem(item.filesystem);
        }

        // Returns the fstype if the item has a filesystem and its unmounted.
        function hasFormattedUnmountedFilesystem(item) {
            if(angular.isObject(item.filesystem) &&
                angular.isString(item.filesystem.fstype) &&
                item.filesystem.fstype !== '' &&
                (angular.isString(item.filesystem.mount_point) === false ||
                    item.filesystem.mount_point === '')) {
                return item.filesystem.fstype;
            }else{
                return null;
            }
        }

        // Return True if the item is in use.
        function isInUse(item) {
            if(item.type === "cache-set") {
                return true;
            } else if(angular.isObject(item.filesystem)) {
                if(item.filesystem.is_format_fstype &&
                    angular.isString(item.filesystem.mount_point) &&
                    item.filesystem.mount_point !== "") {
                    return true;
                } else if(!item.filesystem.is_format_fstype) {
                    return true;
                }
                return false;
            }
            return item.available_size < MIN_PARTITION_SIZE;
        }

        // Return the tags formatted for ngTagInput.
        function getTags(disk) {
            var tags = [];
            angular.forEach(disk.tags, function(tag) {
                tags.push({ text: tag });
            });
            return tags;
        }

        // Return a unique key that will never change.
        function getUniqueKey(disk) {
            if(disk.type === "cache-set") {
                return "cache-set-" + disk.cache_set_id;
            } else {
                var key = disk.type + "-" + disk.block_id;
                if(angular.isNumber(disk.partition_id)) {
                    key += "-" + disk.partition_id;
                }
                return key;
            }
        }

        // Update the list of filesystems. Only filesystems with a mount point
        // set go here. If no mount point is set, it goes in available.
        function updateFilesystems() {
            // Create the new list of filesystems.
            var filesystems = [];
            angular.forEach($scope.node.disks, function(disk) {
                if(hasMountedFilesystem(disk)) {
                    var data = {
                        "type": "filesystem",
                        "name": disk.name,
                        "size_human": disk.size_human,
                        "fstype": disk.filesystem.fstype,
                        "mount_point": disk.filesystem.mount_point,
                        "mount_options": disk.filesystem.mount_options,
                        "block_id": disk.id,
                        "partition_id": null,
                        "original_type": disk.type,
                        "original": disk
                    };
                    if(disk.type === "virtual") {
                        disk.parent_type = disk.parent.type;
                    }
                    filesystems.push(data);
                }
                angular.forEach(disk.partitions, function(partition) {
                    if(hasMountedFilesystem(partition)) {
                        filesystems.push({
                            "type": "filesystem",
                            "name": partition.name,
                            "size_human": partition.size_human,
                            "fstype": partition.filesystem.fstype,
                            "mount_point": partition.filesystem.mount_point,
                            "mount_options":
                                partition.filesystem.mount_options,
                            "block_id": disk.id,
                            "partition_id": partition.id,
                            "original_type": "partition",
                            "original": partition
                        });
                    }
                });
            });

            // Add special filesystems to the filesystem list. A special
            // filesystem cannot exist unless mounted, so we don't need
            // to check.
            angular.forEach(
                $scope.node.special_filesystems,
                function(filesystem) {
                    filesystems.push({
                        "type": "filesystem",
                        "name": "—",
                        "size_human": "—",
                        "fstype": filesystem.fstype,
                        "mount_point": filesystem.mount_point,
                        "mount_options": filesystem.mount_options,
                        "block_id": null,
                        "partition_id": null,
                        "original_type": "special"
                    });
                });

            // Update the selected filesystems with the currently selected
            // filesystems.
            angular.forEach(filesystems, function(filesystem) {
                var key = getUniqueKey(filesystem);
                var oldFilesystem = $scope.filesystemsMap[key];
                if(angular.isObject(oldFilesystem)) {
                    filesystem.$selected = oldFilesystem.$selected;
                } else {
                    filesystem.$selected = false;
                }
            });

            // Update the filesystems and filesystemsMap on the scope.
            $scope.filesystems = filesystems;
            $scope.filesystemsMap = {};
            angular.forEach(filesystems, function(filesystem) {
                $scope.filesystemsMap[getUniqueKey(filesystem)] = filesystem;
            });

            // Update the selection mode.
            $scope.updateFilesystemSelection(false);
        }

        // Update the list of cache sets.
        function updateCacheSets() {
            // Create the new list of cache sets.
            var cachesets = [];
            angular.forEach($scope.node.disks, function(disk) {
                if(disk.type === "cache-set") {
                    cachesets.push({
                        "type": "cache-set",
                        "name": disk.name,
                        "size_human": disk.size_human,
                        "cache_set_id": disk.id,
                        "used_by": disk.used_for
                    });
                }
            });

            // Update the selected cache sets with the currently selected
            // cache sets.
            angular.forEach(cachesets, function(cacheset) {
                var key = getUniqueKey(cacheset);
                var oldCacheSet = $scope.cachesetsMap[key];
                if(angular.isObject(oldCacheSet)) {
                    cacheset.$selected = oldCacheSet.$selected;
                } else {
                    cacheset.$selected = false;
                }
            });

            // Update the cachesets and cachesetsMap on the scope.
            $scope.cachesets = cachesets;
            $scope.cachesetsMap = {};
            angular.forEach(cachesets, function(cacheset) {
                $scope.cachesetsMap[getUniqueKey(cacheset)] = cacheset;
            });

            // Update the selection mode.
            $scope.updateCacheSetsSelection(false);
        }

        // Update list of all available disks.
        function updateAvailable() {
            var available = [];
            angular.forEach($scope.node.disks, function(disk) {
                if(!isInUse(disk)) {
                    var has_partitions = false;
                    if(angular.isArray(disk.partitions) &&
                        disk.partitions.length > 0) {
                        has_partitions = true;
                    }
                    var data = {
                        "name": disk.name,
                        "size_human": disk.size_human,
                        "available_size_human": disk.available_size_human,
                        "used_size_human": disk.used_size_human,
                        "type": disk.type,
                        "model": disk.model,
                        "serial": disk.serial,
                        "tags": getTags(disk),
                        "fstype": hasFormattedUnmountedFilesystem(disk),
                        "mount_point": null,
                        "mount_options": null,
                        "block_id": disk.id,
                        "partition_id": null,
                        "has_partitions": has_partitions,
                        "is_boot": disk.is_boot,
                        "original": disk
                    };
                    if(disk.type === "virtual") {
                        data.parent_type = disk.parent.type;
                    }
                    available.push(data);
                }
                angular.forEach(disk.partitions, function(partition) {
                    if(!isInUse(partition)) {
                        available.push({
                            "name": partition.name,
                            "size_human": partition.size_human,
                            "available_size_human": (
                                partition.available_size_human),
                            "used_size_human": partition.used_size_human,
                            "type": "partition",
                            "model": "",
                            "serial": "",
                            "tags": [],
                            "fstype":
                                hasFormattedUnmountedFilesystem(partition),
                            "mount_point": null,
                            "mount_options": null,
                            "block_id": disk.id,
                            "partition_id": partition.id,
                            "has_partitions": false,
                            "is_boot": false,
                            "original": partition
                        });
                    }
                });
            });

            // Update the selected available disks with the currently selected
            // available disks. Also copy the $options so they are not lost
            // for the current action.
            angular.forEach(available, function(disk) {
                var key = getUniqueKey(disk);
                var oldDisk = $scope.availableMap[key];
                if(angular.isObject(oldDisk)) {
                    disk.$selected = oldDisk.$selected;
                    disk.$options = oldDisk.$options;
                } else {
                    disk.$selected = false;
                    disk.$options = {};
                }
            });

            // Update available and availableMap on the scope.
            $scope.available = available;
            $scope.availableMap = {};
            angular.forEach(available, function(disk) {
                $scope.availableMap[getUniqueKey(disk)] = disk;
            });

            // Update device or devices on the availableNew object to be
            // there new objects.
            if(angular.isObject($scope.availableNew)) {
                // Update device.
                if(angular.isObject($scope.availableNew.device)) {
                    var key = getUniqueKey($scope.availableNew.device);
                    $scope.availableNew.device = $scope.availableMap[key];
                // Update devices.
                } else if(angular.isArray($scope.availableNew.devices)) {
                    var newDevices = [];
                    angular.forEach(
                        $scope.availableNew.devices, function(device) {
                            var key = getUniqueKey(device);
                            var newDevice = $scope.availableMap[key];
                            if(angular.isObject(newDevice)) {
                                newDevices.push(newDevice);
                            }
                        });
                    $scope.availableNew.devices = newDevices;
                }
            }

            // Update the selection mode.
            $scope.updateAvailableSelection(false);
        }

        // Update list of all used disks.
        function updateUsed() {
            var used = [];
            angular.forEach($scope.node.disks, function(disk) {
                if(isInUse(disk) && disk.type !== "cache-set") {
                    var data = {
                        "name": disk.name,
                        "type": disk.type,
                        "model": disk.model,
                        "serial": disk.serial,
                        "tags": getTags(disk),
                        "used_for": disk.used_for,
                        "is_boot": disk.is_boot
                    };
                    if(disk.type === "virtual") {
                        data.parent_type = disk.parent.type;
                    }
                    used.push(data);
                }
                angular.forEach(disk.partitions, function(partition) {
                    if(isInUse(partition) && partition.type !== "cache-set") {
                        used.push({
                            "name": partition.name,
                            "type": "partition",
                            "model": "",
                            "serial": "",
                            "tags": [],
                            "used_for": partition.used_for,
                            "is_boot": false
                        });
                    }
                });
            });
            $scope.used = used;
        }

        // Updates the filesystem, available, and used list.
        function updateDisks() {
            if(angular.isArray($scope.node.disks)) {
                $scope.has_disks = $scope.node.disks.length > 0;
                updateFilesystems();
                updateCacheSets();
                updateAvailable();
                updateUsed();
            } else {
                $scope.has_disks = false;
                $scope.filesystems = [];
                $scope.filesystemsMap = {};
                $scope.filesystemMode = SELECTION_MODE.NONE;
                $scope.filesystemAllSelected = false;
                $scope.cachesets = [];
                $scope.cachesetsMap = {};
                $scope.cachesetsMode = SELECTION_MODE.NONE;
                $scope.cachesetsAllSelected = false;
                $scope.available = [];
                $scope.availableMap = {};
                $scope.availableMode = SELECTION_MODE.NONE;
                $scope.availableAllSelected = false;
                $scope.availableNew = {};
                $scope.used = [];
            }
        }

        // Deselect all items in the array.
        function deselectAll(items) {
            angular.forEach(items, function(item) {
                item.$selected = false;
            });
        }

        // Capitalize the first letter of the string.
        function capitalizeFirstLetter(string) {
            return string.charAt(0).toUpperCase() + string.slice(1);
        }

        // Return true if the string is a number.
        function isNumber(string) {
            var pattern = /^-?\d+\.?\d*$/;
            return pattern.test(string);
        }

        // Extract the index from the name based on prefix.
        function getIndexFromName(prefix, name) {
            var pattern = new RegExp("^" + prefix + "([0-9]+)$");
            var match = pattern.exec(name);
            if(angular.isArray(match) && match.length === 2) {
                return parseInt(match[1], 10);
            }
        }

        // Get the next device name based on prefix.
        function getNextName(prefix) {
            var idx = -1;
            angular.forEach($scope.node.disks, function(disk) {
                var dIdx = getIndexFromName(prefix, disk.name);
                if(angular.isNumber(dIdx)) {
                    idx = Math.max(idx, dIdx);
                }
                angular.forEach(disk.partitions, function(partition) {
                    dIdx = getIndexFromName(prefix, partition.name);
                    if(angular.isNumber(dIdx)) {
                        idx = Math.max(idx, dIdx);
                    }
                });
            });
            return prefix + (idx + 1);
        }

        // Return true if another disk exists with name.
        function isNameAlreadyInUse(name, exclude_disk) {
            if(!angular.isArray($scope.node.disks)) {
                return false;
            }

            var i, j;
            for(i = 0; i < $scope.node.disks.length; i++) {
                var disk = $scope.node.disks[i];
                if(disk.name === name) {
                    if(!angular.isObject(exclude_disk) ||
                        exclude_disk.type === "partition" ||
                        exclude_disk.block_id !== disk.id) {
                        return true;
                    }
                }
                if(angular.isArray(disk.partitions)) {
                    for(j = 0; j < disk.partitions.length; j++) {
                        var partition = disk.partitions[j];
                        if(partition.name === name) {
                            if(!angular.isObject(exclude_disk) ||
                                exclude_disk.type !== "partition" ||
                                exclude_disk.partition_id !== partition.id) {
                                return true;
                            }
                        }
                    }
                }
            }
            return false;
        }

        // Return true if the disk is a logical volume.
        function isLogicalVolume(disk) {
            return disk.type === "virtual" && disk.parent_type === "lvm-vg";
        }

        // Called by $parent when the node has been loaded.
        $scope.nodeLoaded = function() {
            $scope.$watch("node.disks", updateDisks);
        };

        // Return true if the item can be a boot disk.
        $scope.isBootDiskDisabled = function(item, section) {
            if(item.type !== "physical") {
                return true;
            }

            // If the disk is in the used section and does not have any
            // partitions then it cannot be a boot disk. Boot disk either
            // require that it be unused or that some partitions exists
            // on the disk. This is because the boot disk has to have a
            // partition table header.
            if(section === "used") {
                return !item.has_partitions;
            }
            return false;
        };

        // Called to change the disk to a boot disk.
        $scope.setAsBootDisk = function(item) {
            // Do nothing if already the boot disk.
            if(item.is_boot) {
                return;
            }
            // Do nothing if disabled.
            if($scope.isBootDiskDisabled(item)) {
                return;
            }

            MachinesManager.setBootDisk($scope.node, item.block_id);
        };

        // Return array of selected filesystems.
        $scope.getSelectedFilesystems = function() {
            var filesystems = [];
            angular.forEach($scope.filesystems, function(filesystem) {
                if(filesystem.$selected) {
                    filesystems.push(filesystem);
                }
            });
            return filesystems;
        };

        // Update the currect mode for the filesystem section and the all
        // selected value.
        $scope.updateFilesystemSelection = function(force) {
            if(angular.isUndefined(force)) {
                force = false;
            }
            var filesystems = $scope.getSelectedFilesystems();
            if(filesystems.length === 0) {
                $scope.filesystemMode = SELECTION_MODE.NONE;
            } else if(filesystems.length === 1 && force) {
                $scope.filesystemMode = SELECTION_MODE.SINGLE;
            } else if(force) {
                $scope.filesystemMode = SELECTION_MODE.MUTLI;
            }

            if($scope.filesystems.length === 0) {
                $scope.filesystemAllSelected = false;
            } else if(filesystems.length === $scope.filesystems.length) {
                $scope.filesystemAllSelected = true;
            } else {
                $scope.filesystemAllSelected = false;
            }
        };

        // Toggle the selection of the filesystem.
        $scope.toggleFilesystemSelect = function(filesystem) {
            filesystem.$selected = !filesystem.$selected;
            $scope.updateFilesystemSelection(true);
        };

        // Toggle the selection of all filesystems.
        $scope.toggleFilesystemAllSelect = function() {
            angular.forEach($scope.filesystems, function(filesystem) {
                if($scope.filesystemAllSelected) {
                    filesystem.$selected = false;
                } else {
                    filesystem.$selected = true;
                }
            });
            $scope.updateFilesystemSelection(true);
        };

        // Return true if checkboxes in the filesystem section should be
        // disabled.
        $scope.isFilesystemsDisabled = function() {
            return ((
                $scope.filesystemMode !== SELECTION_MODE.NONE &&
                $scope.filesystemMode !== SELECTION_MODE.SINGLE &&
                $scope.filesystemMode !== SELECTION_MODE.MUTLI) ||
                $scope.isAllStorageDisabled());
        };

        // Cancel the current filesystem operation.
        $scope.filesystemCancel = function() {
            $scope.updateFilesystemSelection(true);
        };

        // Enter unmount mode.
        $scope.filesystemUnmount = function() {
            $scope.filesystemMode = SELECTION_MODE.UNMOUNT;
        };

        // Quickly enter unmount by selecting the filesystem first.
        $scope.quickFilesystemUnmount = function(filesystem) {
            deselectAll($scope.filesystems);
            filesystem.$selected = true;
            $scope.updateFilesystemSelection(true);
            $scope.filesystemUnmount();
        };

        // Confirm the unmount action for filesystem.
        $scope.filesystemConfirmUnmount = function(filesystem) {
            MachinesManager.updateFilesystem(
                $scope.node,
                filesystem.block_id, filesystem.partition_id,
                filesystem.fstype, null, null);

            var idx = $scope.filesystems.indexOf(filesystem);
            $scope.filesystems.splice(idx, 1);
            $scope.updateFilesystemSelection();
        };

        // Enter delete mode.
        $scope.filesystemDelete = function() {
            $scope.filesystemMode = SELECTION_MODE.DELETE;
        };

        // Quickly enter delete by selecting the filesystem first.
        $scope.quickFilesystemDelete = function(filesystem) {
            deselectAll($scope.filesystems);
            filesystem.$selected = true;
            $scope.updateFilesystemSelection(true);
            $scope.filesystemDelete();
        };

        // Confirm the delete action for filesystem.
        $scope.filesystemConfirmDelete = function(filesystem) {
            if(filesystem.original_type === "special") {
                // Delete the special filesystem.
                MachinesManager.unmountSpecialFilesystem(
                    $scope.node, filesystem.mount_point);
            } else if(filesystem.original_type === "partition") {
                // Delete the partition.
                MachinesManager.deletePartition(
                    $scope.node, filesystem.original.id);
            } else {
                // Delete the disk.
                MachinesManager.deleteDisk(
                    $scope.node, filesystem.original.id);
            }

            var idx = $scope.filesystems.indexOf(filesystem);
            $scope.filesystems.splice(idx, 1);
            $scope.updateFilesystemSelection();
        };

        // Return true if the disk has an unmouted filesystem.
        $scope.hasUnmountedFilesystem = function(disk) {
            if(angular.isString(disk.fstype) && disk.fstype !== "") {
                if(!angular.isString(disk.mount_point) ||
                    disk.mount_point === "") {
                    return true;
                }
            }
            return false;
        };

        // Return true if the free space label should be shown.
        $scope.showFreeSpace = function(disk) {
            if(disk.type === "lvm-vg") {
                return true;
            } else if(disk.type === "physical" || disk.type === "virtual") {
                return disk.has_partitions;
            } else {
                return false;
            }
        };

        // Return the device type for the disk.
        $scope.getDeviceType = function(disk) {
            if(angular.isUndefined(disk)) {
                return "";
            }

            if(disk.type === "virtual") {
                if(disk.parent_type === "lvm-vg") {
                    return "Logical volume";
                } else if(disk.parent_type.indexOf("raid-") === 0) {
                    return "RAID " + disk.parent_type.split("-")[1];
                } else {
                    return capitalizeFirstLetter(disk.parent_type);
                }
            } else if(disk.type === "lvm-vg") {
                return "Volume group";
            } else {
                return capitalizeFirstLetter(disk.type);
            }
        };

        // Return array of selected available disks.
        $scope.getSelectedAvailable = function() {
            var available = [];
            angular.forEach($scope.available, function(disk) {
                if(disk.$selected) {
                    available.push(disk);
                }
            });
            return available;
        };

        // Update the currect mode for the available section and the all
        // selected value.
        $scope.updateAvailableSelection = function(force) {
            if(angular.isUndefined(force)) {
                force = false;
            }
            var available = $scope.getSelectedAvailable();
            if(available.length === 0) {
                $scope.availableMode = SELECTION_MODE.NONE;
            } else if(available.length === 1 && force) {
                $scope.availableMode = SELECTION_MODE.SINGLE;
            } else if(force) {
                $scope.availableMode = SELECTION_MODE.MUTLI;
            }

            if($scope.available.length === 0) {
                $scope.availableAllSelected = false;
            } else if(available.length === $scope.available.length) {
                $scope.availableAllSelected = true;
            } else {
                $scope.availableAllSelected = false;
            }
        };

        // Toggle the selection of the available disk.
        $scope.toggleAvailableSelect = function(disk) {
            disk.$selected = !disk.$selected;
            $scope.updateAvailableSelection(true);
        };

        // Toggle the selection of all available disks.
        $scope.toggleAvailableAllSelect = function() {
            angular.forEach($scope.available, function(disk) {
                if(!$scope.availableAllSelected) {
                    disk.$selected = true;
                } else {
                    disk.$selected = false;
                }
            });
            $scope.updateAvailableSelection(true);
        };

        // Return true if checkboxes in the avaiable section should be
        // disabled.
        $scope.isAvailableDisabled = function() {
            return ((
                $scope.availableMode !== SELECTION_MODE.NONE &&
                $scope.availableMode !== SELECTION_MODE.SINGLE &&
                $scope.availableMode !== SELECTION_MODE.MUTLI) ||
                $scope.isAllStorageDisabled());
        };

        // Return true if the disk can be formatted and mounted.
        $scope.canFormatAndMount = function(disk) {
            if($scope.isAllStorageDisabled()) {
                return false;
            } else if(disk.type === "lvm-vg" || disk.has_partitions) {
                return false;
            } else if(disk.type === "physical" && disk.original.is_boot) {
                return false;
            } else {
                return true;
            }
        };

        // Return the text for the format and mount button.
        $scope.getFormatAndMountButtonText = function(disk) {
            if($scope.hasUnmountedFilesystem(disk)) {
                return "Mount";
            } else {
                return "Format";
            }
        };

        // Return the text for the partition button.
        $scope.getPartitionButtonText = function(disk) {
            if(disk.has_partitions) {
                return "Add partition";
            } else {
                return "Partition";
            }
        };

        $scope.availablePartitionSpace = function(disk) {
            var space_to_reserve = 0;
            if(!angular.isString(disk.original.partition_table_type)
                || disk.original.partition_table_type === "") {
                // Disk has no partition table, so reserve space for it.
                space_to_reserve = PARTITION_TABLE_EXTRA_SPACE;
                // ppc64el node requires that space be saved for the prep
                // partition.
                if($scope.node.architecture.indexOf("ppc64el") === 0) {
                    space_to_reserve += PREP_PARTITION_SIZE;
                }
            }
            return ConverterService.roundByBlockSize(
                disk.original.available_size - space_to_reserve,
                PARTITION_ALIGNMENT_SIZE);
        };

        // Return true if a partition can be added to disk.
        $scope.canAddPartition = function(disk) {
            if(!$scope.isSuperUser() || $scope.isAllStorageDisabled()) {
                return false;
            } else if(disk.type === "partition" || disk.type === "lvm-vg") {
                return false;
            } else if(disk.type === "virtual" &&
                (disk.parent_type === "lvm-vg" ||
                    disk.parent_type === "bcache")) {
                return false;
            } else if(angular.isString(disk.fstype) && disk.fstype !== "") {
                return false;
            }
            // If we can fit a minimum partition, we're golden.
            return ($scope.availablePartitionSpace(disk) -
                MIN_PARTITION_SIZE) >= 0;
        };

        // Return true if the name is invalid.
        $scope.isNameInvalid = function(disk) {
            if(disk.name === "") {
                return false;
            } else if(isNameAlreadyInUse(disk.name, disk)) {
                return true;
            } else {
                return false;
            }
        };

        // Save the new name of the disk if it changed.
        $scope.saveAvailableName = function(disk) {
            if(disk.name === "") {
                disk.name = disk.original.name;
            } else if(disk.name !== disk.original.name) {
                var name = disk.name;
                if(isLogicalVolume(disk)){
                    var parentName = disk.original.name.split("-")[0] + "-";
                    name = name.slice(parentName.length);
                }
                MachinesManager.updateDisk($scope.node, disk.block_id, {
                    name: name
                });
            }
        };

        // Prevent logical volumes from changing the volume group prefix.
        $scope.nameHasChanged = function(disk) {
            if(isLogicalVolume(disk)) {
                var parentName = disk.original.name.split("-")[0] + "-";
                var startsWith = disk.name.indexOf(parentName);
                if(startsWith !== 0) {
                    disk.name = parentName;
                }
            }
        };

        // Cancel the current available operation.
        $scope.availableCancel = function() {
            $scope.updateAvailableSelection(true);
            $scope.availableNew = {};
        };

        // Enter unformat mode.
        $scope.availableUnformat = function() {
            $scope.availableMode = SELECTION_MODE.UNFORMAT;
        };

        // Confirm the unformat action.
        $scope.availableConfirmUnformat = function(disk) {
            MachinesManager.updateFilesystem(
                $scope.node,
                disk.block_id, disk.partition_id,
                null, null, null);

            // Clear the fstype.
            disk.fstype = null;
            $scope.updateAvailableSelection(true);
        };

        // Enter format and mount mode.
        $scope.availableFormatAndMount = function(disk) {
            disk.$options = {
                fstype: disk.fstype || "ext4",
                mountPoint: disk.mount_point || "",
                mountOptions: disk.mount_options || ""
            };
            $scope.availableMode = SELECTION_MODE.FORMAT_AND_MOUNT;
            // The filesystem type hasn't actually changed, but we call
            // fstypeChanged() to update the mount point and mount
            // options fields to reflect the chosen filesystem.
            $scope.fstypeChanged(disk.$options);
        };

        // Quickly enter the format and mount mode.
        $scope.availableQuickFormatAndMount = function(disk) {
            deselectAll($scope.available);
            disk.$selected = true;
            $scope.updateAvailableSelection(true);
            $scope.availableFormatAndMount(disk);
        };

        // Return the text for the submit button in the format and mount mode.
        $scope.getAvailableFormatSubmitText = function(disk) {
            if(angular.isString(disk.$options.mountPoint) &&
                disk.$options.mountPoint !== "") {
                return "Mount";
            } else {
                return "Format";
            }
        };

        // Confirm the format and mount action.
        $scope.availableConfirmFormatAndMount = function(disk) {
            // Do nothing if its invalid.
            if($scope.isMountPointInvalid(disk.$options.mountPoint)) {
                return;
            }

            // Update the filesystem.
            MachinesManager.updateFilesystem(
                $scope.node, disk.block_id, disk.partition_id,
                disk.$options.fstype, disk.$options.mountPoint,
                disk.$options.mountOptions);

            // Set the options on the object so no flicker occurs while waiting
            // for the new object to be received.
            disk.fstype = disk.$options.fstype;
            disk.mount_point = disk.$options.mountPoint;
            disk.mount_options = disk.$options.mountOptions;
            $scope.updateAvailableSelection(true);

            // If the mount_point is set the we need to transition this to
            // the filesystem section.
            if(angular.isString(disk.mount_point) && disk.mount_point !== "") {
                $scope.filesystems.push({
                    "name": disk.name,
                    "size_human": disk.size_human,
                    "fstype": disk.fstype,
                    "mount_point": disk.mount_point,
                    "mount_options": disk.mount_options,
                    "block_id": disk.block_id,
                    "partition_id": disk.partition_id
                });

                // Remove the selected disk from available.
                var idx = $scope.available.indexOf(disk);
                $scope.available.splice(idx, 1);
                $scope.updateAvailableSelection(true);
            }
        };

        // Return true if the filesystem can be mounted at a directory.
        $scope.usesMountPoint = function(fstype) {
            return angular.isString(fstype) && fstype !== "swap";
        };

        // Return true if the filesystem uses storage (partition or
        // block device).
        $scope.usesStorage = function(fstype) {
            return angular.isString(fstype) &&
                fstype !== "tmpfs" && fstype !== "ramfs";
        };

        // Return true if the mount point is invalid.
        $scope.isMountPointInvalid = function(mountPoint) {
            if(angular.isUndefined(mountPoint) || mountPoint === "") {
                return false;
            } else if (mountPoint === "none") {
                // XXX: Hack to allow "swap" filesystems to be mounted.
                // This should be allowed only when fstype is 'swap' but
                // doing that would require more refactoring (or more
                // hacks) that I have time for right now.
                return false;
            } else if(mountPoint[0] !== "/") {
                return true;
            } else {
                return false;
            }
        };

        // Return true if the disk can be deleted.
        $scope.canDelete = function(disk) {
            if(!$scope.isSuperUser() || $scope.isAllStorageDisabled()) {
                return false;
            } else if(disk.type === "lvm-vg") {
                return disk.original.used_size === 0;
            } else {
                return !disk.has_partitions;
            }
        };

        // Enter unformat mode.
        $scope.availableUnformat = function() {
            $scope.availableMode = SELECTION_MODE.UNFORMAT;
        };

        // Quickly enter unformat mode.
        $scope.availableQuickUnformat = function(disk) {
            deselectAll($scope.available);
            disk.$selected = true;
            $scope.updateAvailableSelection(true);
            $scope.availableUnformat();
        };

        // Enter delete mode.
        $scope.availableDelete = function() {
            $scope.availableMode = SELECTION_MODE.DELETE;
        };

        // Quickly enter delete mode.
        $scope.availableQuickDelete = function(disk) {
            deselectAll($scope.available);
            disk.$selected = true;
            $scope.updateAvailableSelection(true);
            $scope.availableDelete();
        };

        // Return the text for remove confirmation message.
        $scope.getRemoveTypeText = function(disk) {
            if(disk.type === "filesystem") {
                if (angular.isObject(disk.original)) {
                    disk = disk.original;
                } else {
                    return "special filesystem";
                }
            }

            if(disk.type === "physical") {
                return "physical disk";
            } else if(disk.type === "partition") {
                return "partition";
            } else if(disk.type === "lvm-vg") {
                return "volume group";
            } else if(disk.type === "virtual") {
                if(disk.parent_type === "lvm-vg") {
                    return "logical volume";
                } else if(disk.parent_type.indexOf("raid-") === 0) {
                    return "RAID " + disk.parent_type.split("-")[1] + " disk";
                } else {
                    return disk.parent_type + " disk";
                }
            }
        };

        // Delete the disk, partition, or volume group.
        $scope.availableConfirmDelete = function(disk) {
            if(disk.type === "lvm-vg") {
                // Delete the volume group.
                MachinesManager.deleteVolumeGroup(
                    $scope.node, disk.block_id);
            } else if(disk.type === "partition") {
                // Delete the partition.
                MachinesManager.deletePartition(
                    $scope.node, disk.partition_id);
            } else {
                // Delete the disk.
                MachinesManager.deleteDisk(
                    $scope.node, disk.block_id);
            }

            // Remove the selected disk from available.
            var idx = $scope.available.indexOf(disk);
            $scope.available.splice(idx, 1);
            $scope.updateAvailableSelection(true);
        };

        // Enter partition mode.
        $scope.availablePartition = function(disk) {
            $scope.availableMode = SELECTION_MODE.PARTITION;
            // Set starting size to the maximum available space.
            var size_and_units = disk.available_size_human.split(" ");
            disk.$options = {
                size: size_and_units[0],
                sizeUnits: size_and_units[1],
                fstype: null,
                mountPoint: "",
                mountOptions: ""
            };
        };

        // Quickly enter partition mode.
        $scope.availableQuickPartition = function(disk) {
            deselectAll($scope.available);
            disk.$selected = true;
            $scope.updateAvailableSelection(true);
            $scope.availablePartition(disk);
        };

        // Get the new name of the partition.
        $scope.getAddPartitionName = function(disk) {
            var length, partitions = disk.original.partitions;
            if(angular.isArray(partitions)) {
                length = partitions.length;
            } else {
                length = 0;
            }
            if(disk.original.partition_table_type === "mbr" &&
                length > 2) {
                return disk.name + "-part" + (length + 2);
            } else if($scope.node.architecture.indexOf("ppc64el") === 0 &&
                disk.original.is_boot) {
                // Boot disk on ppc64el machines skip the first partition as
                // its reserved for the prep partition.
                return disk.name + "-part" + (length + 2);
            } else {
                return disk.name + "-part" + (length + 1);
            }
        };

        // Return true if the size is invalid.
        $scope.isAddPartitionSizeInvalid = function(disk) {
            if(disk.$options.size === "" || !isNumber(disk.$options.size)) {
                return true;
            } else {
                var bytes = ConverterService.unitsToBytes(
                    disk.$options.size, disk.$options.sizeUnits);
                if(bytes < MIN_PARTITION_SIZE) {
                    return true;
                } else if(bytes > disk.original.available_size) {
                    // Round the size down to the lowest tolerance for that
                    // to see if it now fits.
                    var rounded = ConverterService.roundUnits(
                        disk.$options.size, disk.$options.sizeUnits);
                    if(rounded > disk.original.available_size) {
                        return true;
                    } else {
                        return false;
                    }
                } else {
                    return false;
                }
            }
        };

        // Confirm the partition creation.
        $scope.availableConfirmPartition = function(disk) {
            // Do nothing if not valid.
            if($scope.isAddPartitionSizeInvalid(disk) ||
                $scope.isMountPointInvalid(disk.$options.mountPoint)) {
                return;
            }

            // Get the bytes to create the partition.
            var bytes = ConverterService.unitsToBytes(
                disk.$options.size, disk.$options.sizeUnits);

            // Accepting prefilled defaults means use whole disk (lp:1509535).
            var size_and_units = disk.original.available_size_human.split(" ");
            if(disk.$options.size === size_and_units[0] &&
               disk.$options.sizeUnits === size_and_units[1]) {
                bytes = disk.original.available_size;
            }

            var removeDisk = false;
            var available_space = $scope.availablePartitionSpace(disk);
            if(bytes >= available_space) {
                // Clamp to available space.
                bytes = available_space;
                // Remove the disk if partition uses all the remaining space.
                removeDisk = true;
            }

            // Create the partition.
            var params = {};
            if(angular.isString(disk.$options.fstype) &&
                disk.$options.fstype !== "") {
                params.fstype = disk.$options.fstype;
                if(disk.$options.mountPoint !== "") {
                    params.mount_point = disk.$options.mountPoint;
                    params.mount_options = disk.$options.mountOptions;
                }
            }
            MachinesManager.createPartition(
                $scope.node, disk.block_id, bytes, params);

            // Remove the disk if needed.
            if(removeDisk) {
                var idx = $scope.available.indexOf(disk);
                $scope.available.splice(idx, 1);
            }
            $scope.updateAvailableSelection(true);
        };

        // Return array of selected cache sets.
        $scope.getSelectedCacheSets = function() {
            var cachesets = [];
            angular.forEach($scope.cachesets, function(cacheset) {
                if(cacheset.$selected) {
                    cachesets.push(cacheset);
                }
            });
            return cachesets;
        };

        // Update the currect mode for the cache sets section and the all
        // selected value.
        $scope.updateCacheSetsSelection = function(force) {
            if(angular.isUndefined(force)) {
                force = false;
            }
            var cachesets = $scope.getSelectedCacheSets();
            if(cachesets.length === 0) {
                $scope.cachesetsMode = SELECTION_MODE.NONE;
            } else if(cachesets.length === 1 && force) {
                $scope.cachesetsMode = SELECTION_MODE.SINGLE;
            } else if(force) {
                $scope.cachesetsMode = SELECTION_MODE.MUTLI;
            }

            if($scope.cachesets.length === 0) {
                $scope.cachesetsAllSelected = false;
            } else if(cachesets.length === $scope.cachesets.length) {
                $scope.cachesetsAllSelected = true;
            } else {
                $scope.cachesetsAllSelected = false;
            }
        };

        // Toggle the selection of the filesystem.
        $scope.toggleCacheSetSelect = function(cacheset) {
            cacheset.$selected = !cacheset.$selected;
            $scope.updateCacheSetsSelection(true);
        };

        // Toggle the selection of all filesystems.
        $scope.toggleCacheSetAllSelect = function() {
            angular.forEach($scope.cachesets, function(cacheset) {
                if($scope.cachesetsAllSelected) {
                    cacheset.$selected = false;
                } else {
                    cacheset.$selected = true;
                }
            });
            $scope.updateCacheSetsSelection(true);
        };

        // Return true if checkboxes in the cache sets section should be
        // disabled.
        $scope.isCacheSetsDisabled = function() {
            return ((
                $scope.isAllStorageDisabled() &&
                !$scope.isSuperUser()) || (
                $scope.cachesetsMode !== SELECTION_MODE.NONE &&
                $scope.cachesetsMode !== SELECTION_MODE.SINGLE &&
                $scope.cachesetsMode !== SELECTION_MODE.MUTLI));
        };

        // Cancel the current cache set operation.
        $scope.cacheSetCancel = function() {
            $scope.updateCacheSetsSelection(true);
        };

        // Can delete the cache set.
        $scope.canDeleteCacheSet = function(cacheset) {
            return (cacheset.used_by === "" &&
                    !$scope.isAllStorageDisabled() &&
                    $scope.isSuperUser());
        };

        // Enter delete mode.
        $scope.cacheSetDelete = function() {
            $scope.cachesetsMode = SELECTION_MODE.DELETE;
        };

        // Quickly enter delete by selecting the cache set first.
        $scope.quickCacheSetDelete = function(cacheset) {
            deselectAll($scope.cachesets);
            cacheset.$selected = true;
            $scope.updateCacheSetsSelection(true);
            $scope.cacheSetDelete();
        };

        // Confirm the delete action for cache set.
        $scope.cacheSetConfirmDelete = function(cacheset) {
            MachinesManager.deleteCacheSet(
                $scope.node, cacheset.cache_set_id);

            var idx = $scope.cachesets.indexOf(cacheset);
            $scope.cachesets.splice(idx, 1);
            $scope.updateCacheSetsSelection();
        };

        // Return true if a cache set can be created.
        $scope.canCreateCacheSet = function() {
            if($scope.isAvailableDisabled() || !$scope.isSuperUser()) {
                return false;
            }

            var selected = $scope.getSelectedAvailable();
            if(selected.length === 1) {
                return (
                    !selected[0].has_partitions &&
                    !$scope.hasUnmountedFilesystem(selected[0]) &&
                    selected[0].type !== "lvm-vg");
            }
            return false;
        };

        // Called to create a cache set.
        $scope.createCacheSet = function() {
            if(!$scope.canCreateCacheSet()) {
                return;
            }

            // Create cache set.
            var disk = $scope.getSelectedAvailable()[0];
            MachinesManager.createCacheSet(
                $scope.node, disk.block_id, disk.partition_id);

            // Remove from available.
            var idx = $scope.available.indexOf(disk);
            $scope.available.splice(idx, 1);
        };

        // Return true if a bcache can be created.
        $scope.canCreateBcache = function() {
            if($scope.isAvailableDisabled() || ! $scope.isSuperUser()) {
                return false;
            }

            var selected = $scope.getSelectedAvailable();
            if(selected.length === 1) {
                var allowed = (
                    !$scope.hasUnmountedFilesystem(selected[0]) &&
                    selected[0].type !== "lvm-vg");
                return allowed && $scope.cachesets.length > 0;
            }
            return false;
        };

        // Enter bcache mode.
        $scope.createBcache = function() {
            if(!$scope.canCreateBcache()) {
                return;
            }
            $scope.availableMode = SELECTION_MODE.BCACHE;
            $scope.availableNew = {
                name: getNextName("bcache"),
                device: $scope.getSelectedAvailable()[0],
                cacheset: $scope.cachesets[0],
                cacheMode: "writeback",
                fstype: null,
                mountPoint: "",
                mountOptions: ""
            };
        };

        // Clear mount point when the fstype is changed.
        $scope.fstypeChanged = function(options) {
            if(options.fstype === null) {
                options.mountPoint = "";
                options.mountOptions = "";
            }
            else {
                // Update the mount point to "none" if "swap" is
                // selected, and vice-versa.
                if ($scope.usesMountPoint(options.fstype)) {
                    if (options.mountPoint === "none") {
                        options.mountPoint = "";
                    }
                }
                else {
                    options.mountPoint = "none";
                }
            }
        };

        // Return true when the name of the new disk is invalid.
        $scope.isNewDiskNameInvalid = function() {
            if(!angular.isObject($scope.node) ||
                !angular.isArray($scope.node.disks)) {
                return true;
            }

            if($scope.availableNew.name === "") {
                return true;
            } else {
                var i, j;
                for(i = 0; i < $scope.node.disks.length; i++) {
                    var disk = $scope.node.disks[i];
                    if($scope.availableNew.name === disk.name) {
                        return true;
                    }
                    if(angular.isArray(disk.partitions)) {
                        for(j = 0; j < disk.partitions.length; j++) {
                            var partition = disk.partitions[j];
                            if($scope.availableNew.name === partition.name) {
                                return true;
                            }
                        }
                    }
                }
            }
            return false;
        };

        // Return true if bcache can be saved.
        $scope.createBcacheCanSave = function() {
            return (
                !$scope.isNewDiskNameInvalid() &&
                !$scope.isMountPointInvalid($scope.availableNew.mountPoint));
        };

        // Confirm and create the bcache device.
        $scope.availableConfirmCreateBcache = function() {
            if(!$scope.createBcacheCanSave()) {
                return;
            }

            // Create the bcache.
            var params = {
                name: $scope.availableNew.name,
                cache_set: $scope.availableNew.cacheset.cache_set_id,
                cache_mode: $scope.availableNew.cacheMode
            };
            if($scope.availableNew.device.type === "partition") {
                params.partition_id = $scope.availableNew.device.partition_id;
            } else {
                params.block_id = $scope.availableNew.device.block_id;
            }
            if(angular.isString($scope.availableNew.fstype) &&
                $scope.availableNew.fstype !== "") {
                params.fstype = $scope.availableNew.fstype;
                // XXX: Inconsistent tests of mountPoint/mount_point; in
                // places it's compared to "" (like here), in others
                // it's tested with angular.isDefined(), others with
                // angular.isString(), others angular.isString() ===
                // false. This is *begging* for bugs.
                if($scope.availableNew.mountPoint !== "") {
                    params.mount_point = $scope.availableNew.mountPoint;
                    params.mount_options = $scope.availableNew.mountOptions;
                }
            }
            MachinesManager.createBcache($scope.node, params);

            // Remove device from available.
            var idx = $scope.available.indexOf($scope.availableNew.device);
            $scope.available.splice(idx, 1);
            $scope.availableNew = {};

            // Update the selection.
            $scope.updateAvailableSelection(true);
        };

        // Return true if a RAID can be created.
        $scope.canCreateRAID = function() {
            if($scope.isAvailableDisabled() || !$scope.isSuperUser()) {
                return false;
            }

            var selected = $scope.getSelectedAvailable();
            if(selected.length > 1) {
                var i;
                for(i = 0; i < selected.length; i++) {
                    if($scope.hasUnmountedFilesystem(selected[i])) {
                        return false;
                    } else if(selected[i].type === "lvm-vg") {
                        return false;
                    }
                }
                return true;
            }
            return false;
        };

        // Called to create a RAID.
        $scope.createRAID = function() {
            if(!$scope.canCreateRAID()) {
                return;
            }
            $scope.availableMode = SELECTION_MODE.RAID;
            $scope.availableNew = {
                name: getNextName("md"),
                devices: $scope.getSelectedAvailable(),
                mode: null,
                spares: [],
                fstype: null,
                mountPoint: "",
                mountOptions: ""
            };
            $scope.availableNew.mode = $scope.getAvailableRAIDModes()[0];
        };

        // Get the available RAID modes.
        $scope.getAvailableRAIDModes = function() {
            if(!angular.isObject($scope.availableNew) ||
                !angular.isArray($scope.availableNew.devices)) {
                return [];
            }

            var modes = [];
            angular.forEach(RAID_MODES, function(mode) {
                if($scope.availableNew.devices.length >= mode.min_disks) {
                    modes.push(mode);
                }
            });
            return modes;
        };

        // Return the total number of available spares for the current mode.
        $scope.getTotalNumberOfAvailableSpares = function() {
            var mode = $scope.availableNew.mode;
            if(angular.isUndefined(mode) || !mode.allows_spares) {
                return 0;
            } else {
                var diff = $scope.availableNew.devices.length - mode.min_disks;
                if(diff < 0) {
                    diff = 0;
                }
                return diff;
            }
        };

        // Return the number of remaining spares that can be selected.
        $scope.getNumberOfRemainingSpares = function() {
            var allowed = $scope.getTotalNumberOfAvailableSpares();
            if(allowed <= 0) {
                return 0;
            } else {
                return allowed - $scope.availableNew.spares.length;
            }
        };

        // Return true if the spares column should be shown.
        $scope.showSparesColumn = function() {
            return $scope.getTotalNumberOfAvailableSpares() > 0;
        };

        // Called when the RAID mode is changed to reset the selected spares.
        $scope.RAIDModeChanged = function() {
            $scope.availableNew.spares = [];
        };

        // Return true if the disk is an active RAID member.
        $scope.isActiveRAIDMember = function(disk) {
            if(!angular.isArray($scope.availableNew.spares)) {
                return true;
            } else {
                var idx = $scope.availableNew.spares.indexOf(
                    getUniqueKey(disk));
                return idx === -1;
            }
        };

        // Return true if the disk is a spare RAID member.
        $scope.isSpareRAIDMember = function(disk) {
            return !$scope.isActiveRAIDMember(disk);
        };

        // Set the disk as an active RAID member.
        $scope.setAsActiveRAIDMember = function(disk) {
            var idx = $scope.availableNew.spares.indexOf(getUniqueKey(disk));
            if(idx > -1) {
                $scope.availableNew.spares.splice(idx, 1);
            }
        };

        // Set the disk as a spare RAID member.
        $scope.setAsSpareRAIDMember = function(disk) {
            var key = getUniqueKey(disk);
            var idx = $scope.availableNew.spares.indexOf(key);
            if(idx === -1) {
                $scope.availableNew.spares.push(key);
            }
        };

        // Return the size of the new RAID device.
        $scope.getNewRAIDSize = function() {
            if(angular.isUndefined($scope.availableNew.mode)) {
                return "";
            }
            var calculateSize = $scope.availableNew.mode.calculateSize;
            if(!angular.isFunction(calculateSize)) {
                return "";
            }

            // Get the number of disks and the minimum disk size in the RAID.
            var numDisks = (
                $scope.availableNew.devices.length -
                $scope.availableNew.spares.length);
            var minSize = Number.MAX_VALUE;
            angular.forEach($scope.availableNew.devices, function(device) {
                // Get the size of the device. For a block device it will be
                // at available_size and for a partition it will be at size.
                var deviceSize = (
                    device.original.available_size || device.original.size);
                minSize = Math.min(minSize, deviceSize);
            });

            // Calculate the new size.
            var size = calculateSize(minSize, numDisks);
            return ConverterService.bytesToUnits(size).string;
        };

        // Return true if RAID can be saved.
        $scope.createRAIDCanSave = function() {
            return (
                !$scope.isNewDiskNameInvalid() &&
                !$scope.isMountPointInvalid($scope.availableNew.mountPoint));
        };

        // Confirm and create the RAID device.
        $scope.availableConfirmCreateRAID = function() {
            if(!$scope.createRAIDCanSave()) {
                return;
            }

            // Create the RAID.
            var params = {
                name: $scope.availableNew.name,
                level: $scope.availableNew.mode.level,
                block_devices: [],
                partitions: [],
                spare_devices: [],
                spare_partitions: []
            };
            angular.forEach($scope.availableNew.devices, function(device) {
                if($scope.isActiveRAIDMember(device)) {
                    if(device.type === "partition") {
                        params.partitions.push(device.partition_id);
                    } else {
                        params.block_devices.push(device.block_id);
                    }
                } else {
                    if(device.type === "partition") {
                        params.spare_partitions.push(device.partition_id);
                    } else {
                        params.spare_devices.push(device.block_id);
                    }
                }
            });
            if(angular.isString($scope.availableNew.fstype) &&
                $scope.availableNew.fstype !== "") {
                params.fstype = $scope.availableNew.fstype;
                if($scope.availableNew.mountPoint !== "") {
                    params.mount_point = $scope.availableNew.mountPoint;
                    params.mount_options = $scope.availableNew.mountOptions;
                }
            }
            MachinesManager.createRAID($scope.node, params);

            // Remove devices from available.
            angular.forEach($scope.availableNew.devices, function(device) {
                var idx = $scope.available.indexOf($scope.availableNew.device);
                $scope.available.splice(idx, 1);
            });
            $scope.availableNew = {};

            // Update the selection.
            $scope.updateAvailableSelection(true);
        };

        // Return true if a volume group can be created.
        $scope.canCreateVolumeGroup = function() {
            if($scope.isAvailableDisabled() || !$scope.isSuperUser()) {
                return false;
            }

            var selected = $scope.getSelectedAvailable();
            if(selected.length > 0) {
                var i;
                for(i = 0; i < selected.length; i++) {
                    if(selected[i].has_partitions) {
                        return false;
                    } else if($scope.hasUnmountedFilesystem(selected[i])) {
                        return false;
                    } else if(selected[i].type === "lvm-vg") {
                        return false;
                    }
                }
                return true;
            }
            return false;
        };

        // Called to create a volume group.
        $scope.createVolumeGroup = function() {
            if(!$scope.canCreateVolumeGroup()) {
                return;
            }
            $scope.availableMode = SELECTION_MODE.VOLUME_GROUP;
            $scope.availableNew = {
                name: getNextName("vg"),
                devices: $scope.getSelectedAvailable()
            };
        };

        // Return the size of the new volume group.
        $scope.getNewVolumeGroupSize = function() {
            var total = 0;
            angular.forEach($scope.availableNew.devices, function(device) {
                // Add available_size or size if available_size is not set.
                total += (
                    device.original.available_size || device.original.size);
            });
            return ConverterService.bytesToUnits(total).string;
        };

        // Return true if volume group can be saved.
        $scope.createVolumeGroupCanSave = function() {
            return !$scope.isNewDiskNameInvalid();
        };

        // Confirm and create the volume group device.
        $scope.availableConfirmCreateVolumeGroup = function() {
            if(!$scope.createVolumeGroupCanSave()) {
                return;
            }

            // Create the RAID.
            var params = {
                name: $scope.availableNew.name,
                block_devices: [],
                partitions: []
            };
            angular.forEach($scope.availableNew.devices, function(device) {
                if(device.type === "partition") {
                    params.partitions.push(device.partition_id);
                } else {
                    params.block_devices.push(device.block_id);
                }
            });
            MachinesManager.createVolumeGroup($scope.node, params);

            // Remove devices from available.
            angular.forEach($scope.availableNew.devices, function(device) {
                var idx = $scope.available.indexOf($scope.availableNew.device);
                $scope.available.splice(idx, 1);
            });
            $scope.availableNew = {};

            // Update the selection.
            $scope.updateAvailableSelection(true);
        };

        // Return true if a logical volume can be added to disk.
        $scope.canAddLogicalVolume = function(disk) {
            if(disk.type !== "lvm-vg") {
                return false;
            } else if(disk.original.available_size < MIN_PARTITION_SIZE) {
                return false;
            } else {
                return true;
            }
        };

        // Enter logical volume mode.
        $scope.availableLogicalVolume = function(disk) {
            $scope.availableMode = SELECTION_MODE.LOGICAL_VOLUME;
            // Set starting size to the maximum available space.
            var size_and_units = disk.available_size_human.split(" ");
            var namePrefix = disk.name + "-lv";
            disk.$options = {
                name: getNextName(namePrefix),
                size: size_and_units[0],
                sizeUnits: size_and_units[1]
            };
        };

        // Return true if the name of the logical volume is invalid.
        $scope.isLogicalVolumeNameInvalid = function(disk) {
            if(!angular.isString(disk.$options.name)) {
                return false;
            }
            var startsWith = disk.$options.name.indexOf(disk.name + "-");
            return (
                startsWith !== 0 ||
                disk.$options.name.length <= disk.name.length + 1 ||
                isNameAlreadyInUse(disk.$options.name));
        };

        // Don't allow the name of the logical volume to remove the volume
        // group name.
        $scope.newLogicalVolumeNameChanged = function(disk) {
            if(!angular.isString(disk.$options.name)) {
                return;
            }
            var startsWith = disk.$options.name.indexOf(disk.name + "-");
            if(startsWith !== 0) {
                disk.$options.name = disk.name + "-";
            }
        };

        // Return true if the logical volume size is invalid.
        $scope.isAddLogicalVolumeSizeInvalid = function(disk) {
            // Uses the same logic as the partition size checked.
            return $scope.isAddPartitionSizeInvalid(disk);
        };

        // Confirm the logical volume creation.
        $scope.availableConfirmLogicalVolume = function(disk) {
            // Do nothing if not valid.
            if($scope.isLogicalVolumeNameInvalid(disk) ||
                $scope.isAddLogicalVolumeSizeInvalid(disk) ||
                $scope.isMountPointInvalid(disk.$options.mountPoint)) {
                return;
            }

            // Get the bytes to create the partition.
            var bytes = ConverterService.unitsToBytes(
                disk.$options.size, disk.$options.sizeUnits);

            // Accepting prefilled defaults means use whole disk (lp:1509535).
            var size_and_units = disk.original.available_size_human.split(" ");
            if(disk.$options.size === size_and_units[0] &&
               disk.$options.sizeUnits === size_and_units[1]) {
                bytes = disk.original.available_size;
            }

            // Clamp to available space.
            if(bytes > disk.original.available_size) {
                bytes = disk.original.available_size;
            }

            // Remove the disk if it is going to use all the remaining space.
            var removeDisk = false;
            if(bytes === disk.original.available_size) {
                removeDisk = true;
            }

            // Remove the volume group name from the name.
            var name = disk.$options.name.slice(disk.name.length + 1);

            // Create the logical volume.
            var params = {};
            if(angular.isString(disk.$options.fstype) &&
                disk.$options.fstype !== "") {
                params.fstype = disk.$options.fstype;
                if(disk.$options.mountPoint !== "") {
                    params.mount_point = disk.$options.mountPoint;
                    params.mount_options = disk.$options.mountOptions;
                }
            }
            MachinesManager.createLogicalVolume(
                $scope.node, disk.block_id, name, bytes, params);

            // Remove the disk if needed.
            if(removeDisk) {
                var idx = $scope.available.indexOf(disk);
                $scope.available.splice(idx, 1);
            }
            $scope.updateAvailableSelection(true);
        };

        // Return true when tags can be edited.
        $scope.canEditTags = function(disk) {
            return (disk.type !== "partition" &&
                    disk.type !== "lvm-vg" &&
                    !$scope.isAllStorageDisabled() &&
                    $scope.isSuperUser());
        };

        // Called to enter tag editing mode
        $scope.availableEditTags = function(disk) {
            disk.$options = {
                editingTags: true,
                tags: angular.copy(disk.tags)
            };
        };

        // Called to cancel editing tags.
        $scope.availableCancelTags = function(disk) {
            disk.$options = {};
        };

        // Called to save the tag changes.
        $scope.availableSaveTags = function(disk) {
            var tags = [];
            angular.forEach(disk.$options.tags, function(tag) {
                tags.push(tag.text);
            });
            MachinesManager.updateDiskTags(
                $scope.node, disk.block_id, tags);
            disk.tags = disk.$options.tags;
            disk.$options = {};
        };

        // Returns true if storage cannot be edited.
        // (it can't be changed when the node is in any state other
        //  than Ready or Allocated)
        $scope.isAllStorageDisabled = function() {
            var authUser = UsersManager.getAuthUser();
            if(!angular.isObject(authUser) || !angular.isObject($scope.node) ||
                (!authUser.is_superuser &&
                 authUser.username !== $scope.node.owner)) {
                return true;
            }else if (angular.isObject($scope.node) &&
                ["Ready", "Allocated"].indexOf(
                    $scope.node.status) === -1) {
                // If the node is not ready or allocated, disable storage panel.
                return true;
            } else {
                // The node must be either ready or broken. Enable it.
                return false;
            }
        };

        // Returns true if there are storage layout errors
        $scope.hasStorageLayoutIssues = function() {
            if(angular.isObject($scope.node) &&
                angular.isArray($scope.node.storage_layout_issues)) {
                return $scope.node.storage_layout_issues.length > 0;
            }
            return false;
        };
    }]);
