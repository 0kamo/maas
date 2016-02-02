/* Copyright 2015 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * MAAS Add Device Controller
 */

angular.module('MAAS').controller('AddDeviceController', [
    '$scope', 'DevicesManager', 'SubnetsManager', 'ManagerHelperService',
    'ValidationService', function($scope, DevicesManager, SubnetsManager,
        ManagerHelperService, ValidationService) {

        // Set the addDeviceScope in the parent, so it can call functions
        // in this controller.
        var parentScope = $scope.$parent;
        parentScope.addDeviceScope = $scope;

        // Set initial values.
        $scope.subnets = SubnetsManager.getItems();
        $scope.viewable = false;
        $scope.error = null;

        // Device ip assignment options.
        $scope.ipAssignments = [
            {
                name: "external",
                title: "External"
            },
            {
                name: "dynamic",
                title: "Dynamic"
            },
            {
                name: "static",
                title: "Static"
            }
        ];

        // Makes a new interface.
        function makeInterface() {
            return {
                mac: "",
                ipAssignment: null,
                subnetId: null,
                ipAddress: ""
            };
        }

        // Makes a new device.
        function makeDevice() {
            return {
                name: "",
                interfaces: [makeInterface()]
            };
        }

        // Initial device.
        $scope.device = makeDevice();

        // Converts the device information from how it is held in the UI to
        // how it is handled over the websocket.
        function convertDeviceToProtocol(device) {
            // Return the new object.
            var convertedDevice = {
                hostname: device.name,
                primary_mac: device.interfaces[0].mac,
                extra_macs: [],
                interfaces: []
            };
            var i;
            for(i = 1; i < device.interfaces.length; i++) {
                convertedDevice.extra_macs.push(device.interfaces[i].mac);
            }
            angular.forEach(device.interfaces, function(nic) {
                convertedDevice.interfaces.push({
                    mac: nic.mac,
                    ip_assignment: nic.ipAssignment.name,
                    ip_address: nic.ipAddress,
                    "subnet": nic.subnetId
                });
            });
            return convertedDevice;
        }

        // Called by the parent scope when this controller is viewable.
        $scope.show = function() {
            // Exit early if already viewable.
            if($scope.viewable) {
                return;
            }
            $scope.device = makeDevice();
            $scope.viewable = true;
        };

        // Called by the parent scope when this controller is hidden.
        $scope.hide = function() {
            $scope.viewable = false;

            // Emit the hidden event.
            $scope.$emit('addDeviceHidden');
        };

        // Returns true if the name is in error.
        $scope.nameHasError = function() {
            // If the name is empty don't show error.
            if($scope.device.name.length === 0) {
                return false;
            }
            return !ValidationService.validateHostname($scope.device.name);
        };

        // Returns true if the MAC is in error.
        $scope.macHasError = function(deviceInterface) {
            // If the MAC is empty don't show error.
            if(deviceInterface.mac.length === 0) {
                return false;
            }
            // If the MAC is invalid show error.
            if(!ValidationService.validateMAC(deviceInterface.mac)) {
                return true;
            }
            // If the MAC is the same as another MAC show error.
            var i;
            for(i = 0; i < $scope.device.interfaces.length; i++) {
                var isSelf = $scope.device.interfaces[i] === deviceInterface;
                if(!isSelf &&
                    $scope.device.interfaces[i].mac === deviceInterface.mac) {
                    return true;
                }
            }
            return false;
        };

        // Returns true if the IP address is in error.
        $scope.ipHasError = function(deviceInterface) {
            // If the IP is empty don't show error.
            if(deviceInterface.ipAddress.length === 0) {
                return false;
            }
            // If ip address is invalid, then exit early.
            if(!ValidationService.validateIP(deviceInterface.ipAddress)) {
                return true;
            }
            var i, inNetwork;
            if(angular.isObject(deviceInterface.ipAssignment)){
                if(deviceInterface.ipAssignment.name === "external") {
                    // External IP address cannot be within a known subnet.
                    for(i = 0; i < $scope.subnets.length; i++) {
                        inNetwork = ValidationService.validateIPInNetwork(
                            deviceInterface.ipAddress,
                            $scope.subnets[i].cidr);
                        if(inNetwork) {
                            return true;
                        }
                    }
                } else if(deviceInterface.ipAssignment.name === "static" &&
                    angular.isNumber(deviceInterface.subnetId)) {
                    // Static IP address must be within a subnet.
                    var subnet = SubnetsManager.getItemFromList(
                        deviceInterface.subnetId);
                    inNetwork = ValidationService.validateIPInNetwork(
                        deviceInterface.ipAddress, subnet.cidr);
                    if(!inNetwork) {
                        return true;
                    }
                }
            }
            return false;
        };

        // Return true when the device is missing information or invalid
        // information.
        $scope.deviceHasError = function() {
            if($scope.device.name === '' || $scope.nameHasError()) {
                return true;
            }

            var i;
            for(i = 0; i < $scope.device.interfaces.length; i++) {
                var deviceInterface = $scope.device.interfaces[i];
                if(deviceInterface.mac === '' ||
                    $scope.macHasError(deviceInterface) ||
                    !angular.isObject(deviceInterface.ipAssignment)) {
                    return true;
                }
                var externalIpError = (
                    deviceInterface.ipAssignment.name === "external" && (
                        deviceInterface.ipAddress === '' ||
                        $scope.ipHasError(deviceInterface)));
                var staticIpError = (
                    deviceInterface.ipAssignment.name === "static" && (
                        !angular.isNumber(deviceInterface.subnetId) ||
                        $scope.ipHasError(deviceInterface)));
                if(externalIpError || staticIpError) {
                    return true;
                }
            }
            return false;
        };

        // Adds new interface to device.
        $scope.addInterface = function() {
            $scope.device.interfaces.push(makeInterface());
        };

        // Returns true if the first interface in the device interfaces array.
        $scope.isPrimaryInterface = function(deviceInterface) {
            return $scope.device.interfaces.indexOf(deviceInterface) === 0;
        };

        // Removes the interface from the devices interfaces array.
        $scope.deleteInterface = function(deviceInterface) {
            // Don't remove the primary.
            if($scope.isPrimaryInterface(deviceInterface)) {
                return;
            }
            $scope.device.interfaces.splice(
                $scope.device.interfaces.indexOf(deviceInterface), 1);
        };

        // Called when cancel clicked.
        $scope.cancel = function() {
            $scope.error = null;
            $scope.device = makeDevice();
            $scope.hide();
        };

        // Convert the Python dict error message to displayed message.
        $scope.convertPythonDictToErrorMsg = function(pythonError) {
            var elements = pythonError.match(/'([A-Za-z0-9 \.:_\-]+)'/g);
            var result = '', msg = '';
            for (k=0; k < elements.length; ++k) {
                if (elements.hasOwnProperty(k)) {
                    switch(elements[k]) {
                        case "'hostname'":
                            msg = elements[++k].replace(/'/g,'');
                            result += msg.replace(/^Node/,'Device') + '  ';
                            break;
                        case "'mac_addresses'":
                            msg = elements[++k].replace(/'/g,'');
                            result += msg + '  ';
                            break;
                        default:
                            result += elements[k].replace(/'/g,'');
                    }
                }
            }
            return result;
        };

        // Called when save is clicked.
        $scope.save = function(addAnother) {
            // Do nothing if device in error.
            if($scope.deviceHasError()) {
                return;
            }

            // Clear the error so it can be set again, if it fails to save
            // the device.
            $scope.error = null;

            // Create the device.
            var device = convertDeviceToProtocol($scope.device);
            DevicesManager.create(device).then(function(device) {
                $scope.device = makeDevice();
                if(!addAnother) {
                    // Hide the scope if not adding another.
                    $scope.hide();
                }
            }, function(error) {
                $scope.error = $scope.convertPythonDictToErrorMsg(error);
            });
        };

        // Load subnets to get the available subnets.
        ManagerHelperService.loadManager(SubnetsManager);
    }]);
