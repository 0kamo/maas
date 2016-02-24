/* Copyright 2015 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * MAAS Module
 *
 * Initializes the MAAS module with its required dependencies and sets up
 * the interpolater to use '{$' and '$}' instead of '{{' and '}}' as this
 * conflicts with Django templates.
 */

angular.module('MAAS', ['ngRoute', 'ngCookies', 'ngTagsInput']).config(
    function($interpolateProvider, $routeProvider) {
        $interpolateProvider.startSymbol('{$');
        $interpolateProvider.endSymbol('$}');

        // Setup routes only for the index page, all remaining pages should
        // not use routes. Once all pages are converted to using Angular this
        // will go away. Causing the page to never have to reload.
        var href = angular.element("base").attr('href');
        var path = document.location.pathname;
        if(path[path.length - 1] !== '/') {
            path += '/';
        }
        if(path === href) {
            $routeProvider.
                when('/nodes', {
                    templateUrl: 'static/partials/nodes-list.html',
                    controller: 'NodesListController'
                }).
                when('/node/:type/:system_id', {
                    templateUrl: 'static/partials/node-details.html',
                    controller: 'NodeDetailsController'
                }).
                when('/node/:system_id', {
                    templateUrl: 'static/partials/node-details.html',
                    controller: 'NodeDetailsController'
                }).
                when('/node/:system_id/result/:filename', {
                    templateUrl: 'static/partials/node-result.html',
                    controller: 'NodeResultController'
                }).
                when('/node/:system_id/events', {
                    templateUrl: 'static/partials/node-events.html',
                    controller: 'NodeEventsController'
                }).
                when('/domains', {
                    templateUrl: 'static/partials/domains-list.html',
                    controller: 'DomainsListController'
                }).
                when('/domain/:domain_id', {
                    templateUrl: 'static/partials/domain-details.html',
                    controller: 'DomainDetailsController'
                }).
                when('/subnets', {
                    templateUrl: 'static/partials/subnets-list.html',
                    controller: 'SubnetsListController'
                }).
                when('/networks', {
                    templateUrl: 'static/partials/networks-list.html',
                    controller: 'NetworksListController'
                }).
                when('/subnet/:subnet_id', {
                    templateUrl: 'static/partials/subnet-details.html',
                    controller: 'SubnetDetailsController'
                }).
                when('/vlan/:vlan_id', {
                    templateUrl: 'static/partials/vlan-details.html',
                    controller: 'VLANDetailsController'
                }).
                otherwise({
                    redirectTo: '/nodes'
                });
        }
    });
