/* Copyright 2015-2017 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * MAAS Nodes List Controller
 */

angular.module('MAAS').controller('NodesListController', [
    '$scope', '$interval', '$rootScope', '$routeParams', '$location',
    'MachinesManager', 'DevicesManager', 'ControllersManager',
    'GeneralManager', 'ManagerHelperService', 'SearchService',
    'ZonesManager', 'UsersManager', 'ServicesManager', 'ScriptsManager',
    'SwitchesManager',
    function($scope, $interval, $rootScope, $routeParams, $location,
        MachinesManager, DevicesManager, ControllersManager, GeneralManager,
        ManagerHelperService, SearchService, ZonesManager, UsersManager,
        ServicesManager, ScriptsManager, SwitchesManager) {

        // Mapping of device.ip_assignment to viewable text.
        var DEVICE_IP_ASSIGNMENT = {
            external: "External",
            dynamic: "Dynamic",
            "static": "Static"
        };

        // Set title and page.
        $rootScope.title = "Nodes";
        $rootScope.page = "nodes";

        // Set initial values.
        $scope.nodes = MachinesManager.getItems();
        $scope.zones = ZonesManager.getItems();
        $scope.devices = DevicesManager.getItems();
        $scope.controllers = ControllersManager.getItems();
        $scope.switches = SwitchesManager.getItems();
        $scope.showswitches = $routeParams.switches === 'on';
        $scope.currentpage = "nodes";
        $scope.osinfo = GeneralManager.getData("osinfo");
        $scope.scripts = ScriptsManager.getItems();
        $scope.loading = true;

        $scope.tabs = {};
        // Nodes tab.
        $scope.tabs.nodes = {};
        $scope.tabs.nodes.pagetitle = "Machines";
        $scope.tabs.nodes.currentpage = "nodes";
        $scope.tabs.nodes.manager = MachinesManager;
        $scope.tabs.nodes.previous_search = "";
        $scope.tabs.nodes.search = "";
        $scope.tabs.nodes.searchValid = true;
        $scope.tabs.nodes.selectedItems = MachinesManager.getSelectedItems();
        $scope.tabs.nodes.metadata = MachinesManager.getMetadata();
        $scope.tabs.nodes.filters = SearchService.getEmptyFilter();
        $scope.tabs.nodes.actionOption = null;
        $scope.tabs.nodes.takeActionOptions = GeneralManager.getData(
            "machine_actions");
        $scope.tabs.nodes.actionErrorCount = 0;
        $scope.tabs.nodes.actionProgress = {
            total: 0,
            completed: 0,
            errors: {}
        };
        $scope.tabs.nodes.osSelection = {
            osystem: null,
            release: null,
            hwe_kernel: null
        };
        $scope.tabs.nodes.zoneSelection = null;
        $scope.tabs.nodes.commissionOptions = {
            enableSSH: false,
            skipNetworking: false,
            skipStorage: false
        };
        $scope.tabs.nodes.releaseOptions = {};
        $scope.tabs.nodes.commissioningSelection = [];
        $scope.tabs.nodes.testSelection = [];

        // Device tab.
        $scope.tabs.devices = {};
        $scope.tabs.devices.pagetitle = "Devices";
        $scope.tabs.devices.currentpage = "devices";
        $scope.tabs.devices.manager = DevicesManager;
        $scope.tabs.devices.previous_search = "";
        $scope.tabs.devices.search = "";
        $scope.tabs.devices.searchValid = true;
        $scope.tabs.devices.selectedItems = DevicesManager.getSelectedItems();
        $scope.tabs.devices.filtered_items = [];
        $scope.tabs.devices.predicate = 'fqdn';
        $scope.tabs.devices.allViewableChecked = false;
        $scope.tabs.devices.metadata = DevicesManager.getMetadata();
        $scope.tabs.devices.filters = SearchService.getEmptyFilter();
        $scope.tabs.devices.column = 'fqdn';
        $scope.tabs.devices.actionOption = null;
        $scope.tabs.devices.takeActionOptions = GeneralManager.getData(
            "device_actions");
        $scope.tabs.devices.actionErrorCount = 0;
        $scope.tabs.devices.actionProgress = {
            total: 0,
            completed: 0,
            errors: {}
        };
        $scope.tabs.devices.zoneSelection = null;

        // Controller tab.
        $scope.tabs.controllers = {};
        $scope.tabs.controllers.pagetitle = "Controllers";
        $scope.tabs.controllers.currentpage = "controllers";
        $scope.tabs.controllers.manager = ControllersManager;
        $scope.tabs.controllers.previous_search = "";
        $scope.tabs.controllers.search = "";
        $scope.tabs.controllers.searchValid = true;
        $scope.tabs.controllers.selectedItems =
            ControllersManager.getSelectedItems();
        $scope.tabs.controllers.filtered_items = [];
        $scope.tabs.controllers.predicate = 'fqdn';
        $scope.tabs.controllers.allViewableChecked = false;
        $scope.tabs.controllers.metadata = ControllersManager.getMetadata();
        $scope.tabs.controllers.filters = SearchService.getEmptyFilter();
        $scope.tabs.controllers.column = 'fqdn';
        $scope.tabs.controllers.actionOption = null;
        // Rack controllers contain all options
        $scope.tabs.controllers.takeActionOptions = GeneralManager.getData(
            "rack_controller_actions");
        $scope.tabs.controllers.actionErrorCount = 0;
        $scope.tabs.controllers.actionProgress = {
            total: 0,
            completed: 0,
            errors: {}
        };
        $scope.tabs.controllers.zoneSelection = null;
        $scope.tabs.controllers.syncStatuses = {};
        $scope.tabs.controllers.addController = false;
        $scope.tabs.controllers.registerUrl = MAAS_config.register_url;
        $scope.tabs.controllers.registerSecret = MAAS_config.register_secret;

        // Switch tab.
        $scope.tabs.switches = {};
        $scope.tabs.switches.pagetitle = "Switches";
        $scope.tabs.switches.currentpage = "switches";
        $scope.tabs.switches.manager = SwitchesManager;
        $scope.tabs.switches.previous_search = "";
        $scope.tabs.switches.search = "";
        $scope.tabs.switches.searchValid = true;
        $scope.tabs.switches.selectedItems = SwitchesManager.getSelectedItems();
        $scope.tabs.switches.filtered_items = [];
        $scope.tabs.switches.predicate = 'fqdn';
        $scope.tabs.switches.allViewableChecked = false;
        $scope.tabs.switches.metadata = SwitchesManager.getMetadata();
        $scope.tabs.switches.filters = SearchService.getEmptyFilter();
        $scope.tabs.switches.column = 'fqdn';
        $scope.tabs.switches.actionOption = null;
        // XXX: Which actions should there be?
        $scope.tabs.switches.takeActionOptions = GeneralManager.getData(
            "device_actions");
        $scope.tabs.switches.actionErrorCount = 0;
        $scope.tabs.switches.actionProgress = {
            total: 0,
            completed: 0,
            errors: {}
        };
        $scope.tabs.switches.zoneSelection = null;


        // Options for add hardware dropdown.
        $scope.addHardwareOption = null;
        $scope.addHardwareOptions = [
            {
                name: "machine",
                title: "Machine"
            },
            {
                name: "chassis",
                title: "Chassis"
            }
        ];

        // This will hold the AddHardwareController once it is initialized.
        // The controller will set this variable as it's always a child of
        // this scope.
        $scope.addHardwareScope = null;

        // This will hold the AddDeviceController once it is initialized.
        // The controller will set this variable as it's always a child of
        // this scope.
        $scope.addDeviceScope = null;

        // When the addHardwareScope is hidden it will emit this event. We
        // clear the call to action button, so it can be used again.
        $scope.$on("addHardwareHidden", function() {
            $scope.addHardwareOption = null;
        });

        // Return true if the tab is in viewing selected mode.
        function isViewingSelected(tab) {
            var search = $scope.tabs[tab].search.toLowerCase();
            return search === "in:(selected)" || search === "in:selected";
        }

        // Sets the search bar to only show selected.
        function enterViewSelected(tab) {
            $scope.tabs[tab].previous_search = $scope.tabs[tab].search;
            $scope.tabs[tab].search = "in:(Selected)";
        }

        // Clear search bar from viewing selected.
        function leaveViewSelected(tab) {
            if(isViewingSelected(tab)) {
                $scope.tabs[tab].search = $scope.tabs[tab].previous_search;
                $scope.updateFilters(tab);
            }
        }

        // Called to update `allViewableChecked`.
        function updateAllViewableChecked(tab) {
            // Not checked when the filtered nodes are empty.
            if($scope.tabs[tab].filtered_items.length === 0) {
                $scope.tabs[tab].allViewableChecked = false;
                return;
            }

            // Loop through all filtered nodes and see if all are checked.
            var i;
            for(i = 0; i < $scope.tabs[tab].filtered_items.length; i++) {
                if(!$scope.tabs[tab].filtered_items[i].$selected) {
                    $scope.tabs[tab].allViewableChecked = false;
                    return;
                }
            }
            $scope.tabs[tab].allViewableChecked = true;
        }

        function clearAction(tab) {
            resetActionProgress(tab);
            leaveViewSelected(tab);
            $scope.tabs[tab].actionOption = null;
            $scope.tabs[tab].zoneSelection = null;
            if(tab === "nodes") {
                // Possible for this to be called before the osSelect
                // direction is initialized. In that case it has not
                // created the $reset function on the model object.
                if(angular.isFunction(
                    $scope.tabs[tab].osSelection.$reset)) {
                    $scope.tabs[tab].osSelection.$reset();
                }
                $scope.tabs[tab].commissionOptions.enableSSH = false;
                $scope.tabs[tab].commissionOptions.skipNetworking = false;
                $scope.tabs[tab].commissionOptions.skipStorage = false;
            }
            $scope.tabs[tab].commissioningSelection = [];
            $scope.tabs[tab].testSelection = [];
        }

        // Clear the action if required.
        function shouldClearAction(tab) {
            if($scope.tabs[tab].selectedItems.length === 0) {
                clearAction(tab);
            }
            if($scope.tabs[tab].actionOption && !isViewingSelected(tab)) {
                $scope.tabs[tab].actionOption = null;
            }
        }

        // Called when the filtered_items are updated. Checks if the
        // filtered_items are empty and if the search still matches the
        // previous search. This will reset the search when no nodes match
        // the current filter.
        function removeEmptyFilter(tab) {
            if($scope.tabs[tab].filtered_items.length === 0 &&
                $scope.tabs[tab].search !== "" &&
                $scope.tabs[tab].search === $scope.tabs[tab].previous_search) {
                $scope.tabs[tab].search = "";
                $scope.updateFilters(tab);
            }
        }

        // Update the number of selected items which have an error based on the
        // current selected action.
        function updateActionErrorCount(tab) {
            var i;
            $scope.tabs[tab].actionErrorCount = 0;
            for(i = 0; i < $scope.tabs[tab].selectedItems.length; i++) {
                var supported = $scope.supportsAction(
                    $scope.tabs[tab].selectedItems[i], tab);
                if(!supported) {
                    $scope.tabs[tab].actionErrorCount += 1;
                }
                $scope.tabs[tab].selectedItems[i].action_failed = false;
            }
        }

        // Reset actionProgress on tab to zero.
        function resetActionProgress(tab) {
            var progress = $scope.tabs[tab].actionProgress;
            progress.completed = progress.total = 0;
            progress.errors = {};
        }

        // Add error to action progress and group error messages by nodes.
        function addErrorToActionProgress(tab, error, node) {
            var progress = $scope.tabs[tab].actionProgress;
            progress.completed += 1;
            var nodes = progress.errors[error];
            if(angular.isUndefined(nodes)) {
                progress.errors[error] = [node];
            } else {
                nodes.push(node);
            }
        }

        // After an action has been performed check if we can leave all nodes
        // selected or if an error occured and we should only show the failed
        // nodes.
        function updateSelectedItems(tab) {
            if(!$scope.hasActionsFailed(tab)) {
                if(!$scope.hasActionsInProgress(tab)) {
                     clearAction(tab);
                     enterViewSelected(tab);
                }
                return;
            }
            angular.forEach($scope.tabs[tab].manager.getItems(),
                    function(node) {
                if(node.action_failed === false) {
                    $scope.tabs[tab].manager.unselectItem(node.system_id);
                }
            });
        }

        // Toggles between the current tab.
        $scope.toggleTab = function(tab) {
            $rootScope.title = $scope.tabs[tab].pagetitle;
            $scope.currentpage = tab;
            $location.search('tab', tab);
        };

        // Clear the search bar.
        $scope.clearSearch = function(tab) {
            $scope.tabs[tab].search = "";
            $scope.updateFilters(tab);
        };

        // Mark a node as selected or unselected.
        $scope.toggleChecked = function(node, tab) {
            if(tab !== 'nodes') {
                if($scope.tabs[tab].manager.isSelected(node.system_id)) {
                    $scope.tabs[tab].manager.unselectItem(node.system_id);
                } else {
                    $scope.tabs[tab].manager.selectItem(node.system_id);
                }
                updateAllViewableChecked(tab);
            }
            updateActionErrorCount(tab);
            shouldClearAction(tab);
        };

        // Select all viewable nodes or deselect all viewable nodes.
        $scope.toggleCheckAll = function(tab) {
            if(tab !== 'nodes') {
                if($scope.tabs[tab].allViewableChecked) {
                    angular.forEach(
                        $scope.tabs[tab].filtered_items, function(node) {
                            $scope.tabs[tab].manager.unselectItem(
                                node.system_id);
                    });
                } else {
                    angular.forEach(
                        $scope.tabs[tab].filtered_items, function(node) {
                            $scope.tabs[tab].manager.selectItem(
                                node.system_id);
                    });
                }
                updateAllViewableChecked(tab);
            }
            updateActionErrorCount(tab);
            shouldClearAction(tab);
        };

        $scope.onMachineListingChanged = function(machines) {
          if(machines.length === 0 &&
              $scope.tabs.nodes.search !== "" &&
              $scope.tabs.nodes.search === $scope.tabs.nodes.previous_search) {
              $scope.tabs.nodes.search = "";
              $scope.updateFilters('nodes');
          }
        };

        // When the filtered nodes change update if all check buttons
        // should be checked or not.
        $scope.$watchCollection("tabs.devices.filtered_items", function() {
            updateAllViewableChecked("devices");
            removeEmptyFilter("devices");
        });
        $scope.$watchCollection("tabs.controllers.filtered_items", function() {
            updateAllViewableChecked("controllers");
            removeEmptyFilter("controllers");
        });
        $scope.$watchCollection("tabs.switches.filtered_items", function() {
            updateAllViewableChecked("switches");
            removeEmptyFilter("switches");
        });

        // Shows the current selection.
        $scope.showSelected = function(tab) {
            enterViewSelected(tab);
            $scope.updateFilters(tab);
        };

        // Adds or removes a filter to the search.
        $scope.toggleFilter = function(type, value, tab) {
            // Don't allow a filter to be changed when an action is
            // in progress.
            if(angular.isObject($scope.tabs[tab].actionOption)) {
                return;
            }
            $scope.tabs[tab].filters = SearchService.toggleFilter(
                $scope.tabs[tab].filters, type, value, true);
            $scope.tabs[tab].search = SearchService.filtersToString(
                $scope.tabs[tab].filters);
        };

        // Return True if the filter is active.
        $scope.isFilterActive = function(type, value, tab) {
            return SearchService.isFilterActive(
                $scope.tabs[tab].filters, type, value, true);
        };

        // Update the filters object when the search bar is updated.
        $scope.updateFilters = function(tab) {
            var filters = SearchService.getCurrentFilters(
                $scope.tabs[tab].search);
            if(filters === null) {
                $scope.tabs[tab].filters = SearchService.getEmptyFilter();
                $scope.tabs[tab].searchValid = false;
            } else {
                $scope.tabs[tab].filters = filters;
                $scope.tabs[tab].searchValid = true;
            }
            shouldClearAction(tab);
        };

        // Sorts the table by predicate.
        $scope.sortTable = function(predicate, tab) {
            $scope.tabs[tab].predicate = predicate;
            $scope.tabs[tab].reverse = !$scope.tabs[tab].reverse;
        };

        // Sets the viewable column or sorts.
        $scope.selectColumnOrSort = function(predicate, tab) {
            if($scope.tabs[tab].column !== predicate) {
                $scope.tabs[tab].column = predicate;
            } else {
                $scope.sortTable(predicate, tab);
            }
        };

        // Return True if the node supports the action.
        $scope.supportsAction = function(node, tab) {
            if(!$scope.tabs[tab].actionOption) {
                return true;
            }
            return node.actions.indexOf(
                $scope.tabs[tab].actionOption.name) >= 0;
        };

        // Called when the action option gets changed.
        $scope.actionOptionSelected = function(tab) {
            updateActionErrorCount(tab);
            enterViewSelected(tab);

            // Hide the add hardware/device section.
            if (tab === 'nodes') {
                if(angular.isObject($scope.addHardwareScope)) {
                    $scope.addHardwareScope.hide();
                }
            } else if(tab === 'devices') {
                if(angular.isObject($scope.addDeviceScope)) {
                    $scope.addDeviceScope.hide();
                }
            }
        };

        // Return True if there is an action error.
        $scope.isActionError = function(tab) {
            if(angular.isObject($scope.tabs[tab].actionOption) &&
                $scope.tabs[tab].actionOption.name === "deploy" &&
                $scope.tabs[tab].actionErrorCount === 0 &&
                ($scope.osinfo.osystems.length === 0 ||
                UsersManager.getSSHKeyCount() === 0)) {
                return true;
            }
            return $scope.tabs[tab].actionErrorCount !== 0;
        };

        // Return True if unable to deploy because of missing images.
        $scope.isDeployError = function(tab) {
            if($scope.tabs[tab].actionErrorCount !== 0) {
                return false;
            }
            if(angular.isObject($scope.tabs[tab].actionOption) &&
                $scope.tabs[tab].actionOption.name === "deploy" &&
                $scope.osinfo.osystems.length === 0) {
                return true;
            }
            return false;
        };

        // Return True if unable to deploy because of missing ssh keys.
        $scope.isSSHKeyError = function(tab) {
            if($scope.tabs[tab].actionErrorCount !== 0) {
                return false;
            }
            if(angular.isObject($scope.tabs[tab].actionOption) &&
                $scope.tabs[tab].actionOption.name === "deploy" &&
                UsersManager.getSSHKeyCount() === 0) {
                return true;
            }
            return false;
        };

        // Called when the current action is cancelled.
        $scope.actionCancel = function(tab) {
            resetActionProgress(tab);
            leaveViewSelected(tab);
            $scope.tabs[tab].actionOption = null;
        };

        // Perform the action on all nodes.
        $scope.actionGo = function(tab) {
            var extra = {};
            var i;
            // Set deploy parameters if a deploy or set zone action.
            if($scope.tabs[tab].actionOption.name === "deploy" &&
                angular.isString($scope.tabs[tab].osSelection.osystem) &&
                angular.isString($scope.tabs[tab].osSelection.release)) {

                // Set extra. UI side the release is structured os/release, but
                // when it is sent over the websocket only the "release" is
                // sent.
                extra.osystem = $scope.tabs[tab].osSelection.osystem;
                var release = $scope.tabs[tab].osSelection.release;
                release = release.split("/");
                release = release[release.length-1];
                extra.distro_series = release;
                // hwe_kernel is optional so only include it if its specified
                if(angular.isString($scope.tabs[tab].osSelection.hwe_kernel) &&
                   ($scope.tabs[tab].osSelection.hwe_kernel.indexOf('hwe-')
                    >= 0 ||
                    $scope.tabs[tab].osSelection.hwe_kernel.indexOf('ga-')
                    >= 0)) {
                    extra.hwe_kernel = $scope.tabs[tab].osSelection.hwe_kernel;
                }
            } else if($scope.tabs[tab].actionOption.name === "set-zone" &&
                angular.isNumber($scope.tabs[tab].zoneSelection.id)) {
                // Set the zone parameter.
                extra.zone_id = $scope.tabs[tab].zoneSelection.id;
            } else if($scope.tabs[tab].actionOption.name === "commission") {
                // Set the commission options.
                extra.enable_ssh = (
                    $scope.tabs[tab].commissionOptions.enableSSH);
                extra.skip_networking = (
                    $scope.tabs[tab].commissionOptions.skipNetworking);
                extra.skip_storage = (
                    $scope.tabs[tab].commissionOptions.skipStorage);
                extra.commissioning_scripts = [];
                for(i=0;i<$scope.tabs[tab].commissioningSelection.length;i++) {
                    extra.commissioning_scripts.push(
                        $scope.tabs[tab].commissioningSelection[i].id);
                }
                if(extra.commissioning_scripts.length === 0) {
                    // Tell the region not to run any custom commissioning
                    // scripts.
                    extra.commissioning_scripts.push('none');
                }
                extra.testing_scripts = [];
                for(i=0;i<$scope.tabs[tab].testSelection.length;i++) {
                    extra.testing_scripts.push(
                        $scope.tabs[tab].testSelection[i].id);
                }
                if(extra.testing_scripts.length === 0) {
                    // Tell the region not to run any tests.
                    extra.testing_scripts.push('none');
                }
            } else if($scope.tabs[tab].actionOption.name === "test") {
                // Set the test options.
                extra.enable_ssh = (
                    $scope.tabs[tab].commissionOptions.enableSSH);
                extra.testing_scripts = [];
                for(i=0;i<$scope.tabs[tab].testSelection.length;i++) {
                    extra.testing_scripts.push(
                        $scope.tabs[tab].testSelection[i].id);
                }
                if(extra.testing_scripts.length === 0) {
                    // Tell the region not to run any tests.
                    extra.testing_scripts.push('none');
                }
            } else if($scope.tabs[tab].actionOption.name === "release") {
                // Set the release options.
                extra.erase = (
                    $scope.tabs[tab].releaseOptions.erase);
                extra.secure_erase = (
                    $scope.tabs[tab].releaseOptions.secureErase);
                extra.quick_erase = (
                    $scope.tabs[tab].releaseOptions.quickErase);
            }

            // Setup actionProgress.
            resetActionProgress(tab);
            $scope.tabs[tab].actionProgress.total =
                $scope.tabs[tab].selectedItems.length;

            // Perform the action on all selected items.
            angular.forEach($scope.tabs[tab].selectedItems, function(node) {
                $scope.tabs[tab].manager.performAction(
                    node, $scope.tabs[tab].actionOption.name,
                    extra).then(function() {
                        $scope.tabs[tab].actionProgress.completed += 1;
                        node.action_failed = false;
                        updateSelectedItems(tab);
                    }, function(error) {
                        addErrorToActionProgress(tab, error, node);
                        node.action_failed = true;
                        updateSelectedItems(tab);
                    });
            });
        };

        // Returns true when actions are being performed.
        $scope.hasActionsInProgress = function(tab) {
            var progress = $scope.tabs[tab].actionProgress;
            return progress.total > 0 && progress.completed !== progress.total;
        };

        // Returns true if any of the actions have failed.
        $scope.hasActionsFailed = function(tab) {
            return Object.keys(
                $scope.tabs[tab].actionProgress.errors).length > 0;
        };

        // Called to when the addHardwareOption has changed.
        $scope.addHardwareOptionChanged = function() {
            if($scope.addHardwareOption) {
                $scope.addHardwareScope.show(
                    $scope.addHardwareOption.name);
            }
        };

        // Called when the add device button is pressed.
        $scope.addDevice = function() {
            $scope.addDeviceScope.show();
        };

        // Called when the cancel add device button is pressed.
        $scope.cancelAddDevice = function() {
            $scope.addDeviceScope.cancel();
        };

        // Get the display text for device ip assignment type.
        $scope.getDeviceIPAssignment = function(ipAssignment) {
            return DEVICE_IP_ASSIGNMENT[ipAssignment];
        };

        // Return true if the authenticated user is super user.
        $scope.isSuperUser = function() {
            return UsersManager.isSuperUser();
        };

        $scope.hasCustomCommissioningScripts = function() {
            var i;
            for(i=0;i<$scope.scripts.length;i++) {
                if($scope.scripts[i].script_type === 0) {
                    return true;
                }
            }
            return false;
        };

        // Load the required managers for this controller. The ServicesManager
        // is required by the maasControllerStatus directive that is used
        // in the partial for this controller.
        ManagerHelperService.loadManagers($scope, [
            MachinesManager, DevicesManager, ControllersManager,
            GeneralManager, ZonesManager, UsersManager, ServicesManager,
            ScriptsManager, SwitchesManager]).then(function() {
                $scope.loading = false;
            });

        // Start polling for the os information.
        GeneralManager.startPolling($scope, "osinfo");

        // Stop polling and save the current filter when the scope is destroyed.
        $scope.$on("$destroy", function() {
            $interval.cancel($scope.statusPoll);
            GeneralManager.stopPolling($scope, "osinfo");
            SearchService.storeFilters("nodes", $scope.tabs.nodes.filters);
            SearchService.storeFilters("devices", $scope.tabs.devices.filters);
            SearchService.storeFilters(
                "controllers", $scope.tabs.controllers.filters);
            SearchService.storeFilters(
                "switches", $scope.tabs.switches.filters);
        });

        // Restore the filters if any saved.
        var nodesFilter = SearchService.retrieveFilters("nodes");
        if(angular.isObject(nodesFilter)) {
            $scope.tabs.nodes.search = SearchService.filtersToString(
                nodesFilter);
            $scope.updateFilters("nodes");
        }
        var devicesFilter = SearchService.retrieveFilters("devices");
        if(angular.isObject(devicesFilter)) {
            $scope.tabs.devices.search = SearchService.filtersToString(
                devicesFilter);
            $scope.updateFilters("devices");
        }
        var controllersFilter = SearchService.retrieveFilters("controllers");
        if(angular.isObject(controllersFilter)) {
            $scope.tabs.controllers.search = SearchService.filtersToString(
                controllersFilter);
            $scope.updateFilters("controllers");
        }
        var switchesFilter = SearchService.retrieveFilters("switches");
        if(angular.isObject(switchesFilter)) {
            $scope.tabs.switches.search = SearchService.filtersToString(
                switchesFilter);
            $scope.updateFilters("switches");
        }


        // Switch to the specified tab, if specified.
        if($routeParams.tab === "nodes" || $routeParams.tab === "devices" ||
                $routeParams.tab === "controllers" ||
                $routeParams.tab === "switches") {
            $scope.toggleTab($routeParams.tab);
        }

        // Set the query if the present in $routeParams.
        var query = $routeParams.query;
        if(angular.isString(query)) {
            $scope.tabs[$scope.currentpage].search = query;
            $scope.updateFilters($scope.currentpage);
        }
    }]);
