/* Copyright 2016 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * MAAS Controllers Manager
 *
 * Manages all of the controllers in the browser. This manager is used for the
 * controllers listing and the controller view page. The manager uses the
 * RegionConnection to load the controllers, update the controllers, and listen
 * for notification events about controllers.
 */

angular.module('MAAS').factory(
    'ControllersManager',
    ['$q', '$rootScope', 'RegionConnection', 'Manager', function(
            $q, $rootScope, RegionConnection, Manager) {

        function ControllersManager() {
            Manager.call(this);

            this._pk = "system_id";
            this._handler = "controller";

            // Listen for notify events for the machine object.
            var self = this;
            RegionConnection.registerNotifier("machine",
                function(action, data) {
                    self.onNotify(action, data);
                });
        }

        ControllersManager.prototype = new Manager();

        // Perform the action on the controller.
        ControllersManager.prototype.performAction = function(
            controller, action, extra) {
            if(!angular.isObject(extra)) {
                extra = {};
            }
            return RegionConnection.callMethod("controller.action", {
                "system_id": controller.system_id,
                "action": action,
                "extra": extra
                });
        };

        // Create the physical interface on the controller.
        ControllersManager.prototype.createPhysicalInterface = function(
            controller, params) {
                if(!angular.isObject(params)) {
                    params = {};
                }
                params.system_id = controller.system_id;
                return RegionConnection.callMethod(
                    "controller.create_physical", params);
            };

        // Create the VLAN interface on the controller.
        ControllersManager.prototype.createVLANInterface = function(
            controller, params) {
                if(!angular.isObject(params)) {
                    params = {};
                }
                params.system_id = controller.system_id;
                return RegionConnection.callMethod(
                    "controller.create_vlan", params);
            };

        // Create the bond interface on the controller.
        ControllersManager.prototype.createBondInterface = function(
            controller, params) {
                if(!angular.isObject(params)) {
                    params = {};
                }
                params.system_id = controller.system_id;
                return RegionConnection.callMethod(
                    "controller.create_bond", params);
            };

        // Update the interface for the controller.
        ControllersManager.prototype.updateInterface = function(
            controller, interface_id, params) {
                if(!angular.isObject(params)) {
                    params = {};
                }
                params.system_id = controller.system_id;
                params.interface_id = interface_id;
                return RegionConnection.callMethod(
                    "controller.update_interface", params);
            };

        // Delete the interface for the controller.
        ControllersManager.prototype.deleteInterface = function(
            controller, interface_id) {
                var params = {
                    system_id: controller.system_id,
                    interface_id: interface_id
                };
                return RegionConnection.callMethod(
                    "controller.delete_interface", params);
            };

        // Create or update the link to the subnet for the interface.
        ControllersManager.prototype.linkSubnet = function(
            controller, interface_id, params) {
                if(!angular.isObject(params)) {
                    params = {};
                }
                params.system_id = controller.system_id;
                params.interface_id = interface_id;
                return RegionConnection.callMethod(
                    "controller.link_subnet", params);
            };

        // Remove the link to the subnet for the interface.
        ControllersManager.prototype.unlinkSubnet = function(
            controller, interface_id, link_id) {
                var params = {
                    system_id: controller.system_id,
                    interface_id: interface_id,
                    link_id: link_id
                };
                return RegionConnection.callMethod(
                    "controller.unlink_subnet", params);
            };

        return new ControllersManager();
    }]);
