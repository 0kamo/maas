/* Copyright 2017 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * Notifications.
 */

angular.module('MAAS').run(['$templateCache', function ($templateCache) {
    // Inject notifications.html into the template cache.
    $templateCache.put('directive/templates/notifications.html', [
      '<div data-ng-repeat="category in categories"',
        ' data-ng-init="notifications = categoryNotifications[category]">',
        // 1 notification.
        '<div class="row" data-ng-if="notifications.length == 1">',
          '<ul class="p-list" data-ng-class="{\'is-open\': shown}">',
            '<li data-ng-repeat="notification in notifications"',
            ' class="p-notification" data-ng-class="categoryClasses[notification.category]">',
              '<p class="p-notification__response">',
                '<span data-ng-bind-html="notification.message"></span> ',
                '<a class="p-notification__action"',
                ' data-ng-click="dismiss(notification)">Dismiss</a>',
              '</p>',
            '</li>',
          '</ul>',
        '</div>',
        // 2 or more notifications.
        '<div class="row" data-ng-if="notifications.length >= 2"',
        ' data-ng-init="shown = false"',
        ' class="p-notification--group">',
          '<button class="p-notification__response" tabindex="0"',
            ' aria-label="{$ notifications.length $} ',
            '{$ categoryTitles[category] $}, click to open messages."',
            ' maas-enter="shown = !shown"',
            ' data-ng-click="shown = !shown">',
            '<span class="p-notification__status"',
            ' data-count="{$ notifications.length $}"',
            ' data-ng-bind="categoryTitles[category]"></span>',
            '<i data-ng-class="{ \'p-icon--plus\': !shown,',
            ' \'p-icon--minus\': shown }"></i>',
          '</button>',
          '<ul class="p-list" data-ng-class="{\'u-hide\': !shown}">',
            '<li data-ng-repeat="notification in notifications"',
            ' class="p-list__item p-notification" data-ng-class="categoryClasses[notification.category]">',
              '<p class="p-notification__response">',
                '<span data-ng-bind-html="notification.message"></span> ',
                '<a class="p-notification__action"',
                ' data-ng-click="dismiss(notification)">Dismiss</a>',
              '</p>',
            '</li>',
          '</ul>',
        '</div>',
      '</div>'
    ].join(''));
}]);

angular.module('MAAS').directive('maasNotifications', [
    "NotificationsManager", "ManagerHelperService",
    function(NotificationsManager, ManagerHelperService) {
        return {
            restrict: "E",
            templateUrl: 'directive/templates/notifications.html',
            link: function($scope, element, attrs) {
                ManagerHelperService.loadManager($scope, NotificationsManager);
                $scope.notifications = NotificationsManager.getItems();
                $scope.dismiss = angular.bind(
                    NotificationsManager, NotificationsManager.dismiss);

                $scope.categories = [
                    "error",
                    "warning",
                    "success",
                    "info"
                ];
                $scope.categoryTitles = {
                    error: "Errors",
                    warning: "Warnings",
                    success: "Successes",
                    info: "Other messages"
                };
                $scope.categoryClasses = {
                    error: "p-notification--negative",
                    warning: "p-notification--caution",
                    success: "p-notification--positive",
                    info: "p-notification"  // No suffix.
                };
                $scope.categoryNotifications = {
                    error: [],
                    warning: [],
                    success: [],
                    info: []
                };

                $scope.$watchCollection(
                    "notifications", function() {
                        var cns = $scope.categoryNotifications;
                        angular.forEach(
                            $scope.categories, function(category) {
                                cns[category].length = 0;
                            }
                        );
                        angular.forEach(
                            $scope.notifications, function(notification) {
                                cns[notification.category].push(notification);
                            }
                        );
                    }
                );
            }
        };
    }]);
