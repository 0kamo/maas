/* Copyright 2016 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * Unit tests for NodesManager. As NodesManager is abstract, we test by
 * instatiating a MachinesManager, which is a subclass of NodesManager.
 */


describe("NodesManager", function() {

    // Load the MAAS module.
    beforeEach(module("MAAS"));

    // Load the MachinesManager and RegionConnection factory.
    var MachinesManager, RegionConnection, webSocket;
    beforeEach(inject(function($injector) {
        MachinesManager = $injector.get("MachinesManager");
        RegionConnection = $injector.get("RegionConnection");

        // Mock buildSocket so an actual connection is not made.
        webSocket = new MockWebSocket();
        spyOn(RegionConnection, "buildSocket").and.returnValue(webSocket);
    }));

    // Open the connection to the region before each test.
    beforeEach(function(done) {
        RegionConnection.registerHandler("open", function() {
            done();
        });
        RegionConnection.connect("");
    });

    // Make a random machine.
    function makemachine(selected) {
        var machine = {
            system_id: makeName("system_id"),
            name: makeName("name"),
            status: makeName("status"),
            owner: makeName("owner")
        };
        if(angular.isDefined(selected)) {
            machine.$selected = selected;
        }
        return machine;
    }

    it("sanity check", function() {
        expect(MachinesManager._pk).toBe("system_id");
        expect(MachinesManager._handler).toBe("machine");
    });

    describe("create", function() {

        it("calls machine.create with machine", function(done) {
            var machine = makemachine();
            webSocket.returnData.push(makeFakeResponse(machine));
            MachinesManager.create(machine).then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create");
                expect(sentObject.params).toEqual(machine);
                done();
            });
        });
    });

    describe("performAction", function() {

        it("calls machine.action with system_id and action", function(done) {
            var machine = makemachine();
            webSocket.returnData.push(makeFakeResponse("deleted"));
            MachinesManager.performAction(machine, "delete").then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.action");
                expect(sentObject.params.system_id).toBe(machine.system_id);
                expect(sentObject.params.action).toBe("delete");
                expect(sentObject.params.extra).toEqual({});
                done();
            });
        });

        it("calls machine.action with extra", function(done) {
            var machine = makemachine();
            var extra = {
                osystem: makeName("os")
            };
            webSocket.returnData.push(makeFakeResponse("deployed"));
            MachinesManager.performAction(
                machine, "deploy", extra).then(function() {
                    var sentObject = angular.fromJson(webSocket.sentData[0]);
                    expect(sentObject.method).toBe("machine.action");
                    expect(sentObject.params.system_id).toBe(machine.system_id);
                    expect(sentObject.params.action).toBe("deploy");
                    expect(sentObject.params.extra).toEqual(extra);
                    done();
                });
        });
    });

    describe("checkPowerState", function() {

        it("calls machine.check_power with system_id", function(done) {
            var machine = makemachine();
            webSocket.returnData.push(makeFakeResponse("on"));
            MachinesManager.checkPowerState(machine).then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.check_power");
                expect(sentObject.params.system_id).toBe(machine.system_id);
                done();
            });
        });

        it("sets power_state to results", function(done) {
            var machine = makemachine();
            var power_state = makeName("state");
            webSocket.returnData.push(makeFakeResponse(power_state));
            MachinesManager.checkPowerState(machine).then(function(state) {
                expect(machine.power_state).toBe(power_state);
                expect(state).toBe(power_state);
                done();
            });
        });

        it("sets power_state to error on error and logs error",
            function(done) {
                var machine = makemachine();
                var error = makeName("error");
                spyOn(console, "log");
                webSocket.returnData.push(makeFakeResponse(error, true));
                MachinesManager.checkPowerState(machine).then(function(state) {
                    expect(machine.power_state).toBe("error");
                    expect(state).toBe("error");
                    expect(console.log).toHaveBeenCalledWith(error);
                    done();
                });
            });
    });

    describe("createPhysicalInterface", function() {

        it("calls machine.create_physical with system_id without params",
            function(done) {
                var machine = makemachine();
                webSocket.returnData.push(makeFakeResponse("created"));
                MachinesManager.createPhysicalInterface(machine).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.create_physical");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        done();
                    });
            });

        it("calls machine.create_physical with params",
            function(done) {
                var machine = makemachine();
                var params = {
                    vlan: makeInteger(0, 100)
                };
                webSocket.returnData.push(makeFakeResponse("created"));
                MachinesManager.createPhysicalInterface(machine, params).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.create_physical");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.vlan).toBe(params.vlan);
                        done();
                    });
            });
    });

    describe("createVLANInterface", function() {

        it("calls machine.create_vlan with system_id without params",
            function(done) {
                var machine = makemachine();
                webSocket.returnData.push(makeFakeResponse("created"));
                MachinesManager.createVLANInterface(machine).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.create_vlan");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        done();
                    });
            });

        it("calls machine.create_vlan with params",
            function(done) {
                var machine = makemachine();
                var params = {
                    vlan: makeInteger(0, 100)
                };
                webSocket.returnData.push(makeFakeResponse("created"));
                MachinesManager.createVLANInterface(machine, params).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.create_vlan");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.vlan).toBe(params.vlan);
                        done();
                    });
            });
    });

    describe("createBondInterface", function() {

        it("calls machine.create_bond with system_id without params",
            function(done) {
                var machine = makemachine();
                webSocket.returnData.push(makeFakeResponse("created"));
                MachinesManager.createBondInterface(machine).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.create_bond");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        done();
                    });
            });

        it("calls machine.create_bond with params",
            function(done) {
                var machine = makemachine();
                var params = {
                    vlan: makeInteger(0, 100)
                };
                webSocket.returnData.push(makeFakeResponse("created"));
                MachinesManager.createBondInterface(machine, params).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.create_bond");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.vlan).toBe(params.vlan);
                        done();
                    });
            });
    });

    describe("updateInterface", function() {

        it("calls machine.update_interface with system_id and interface_id",
            function(done) {
                var machine = makemachine(), interface_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("updated"));
                MachinesManager.updateInterface(machine, interface_id).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.update_interface");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.interface_id).toBe(
                            interface_id);
                        done();
                    });
            });

        it("calls machine.update_interface with params",
            function(done) {
                var machine = makemachine(), interface_id = makeInteger(0, 100);
                var params = {
                    name: makeName("eth0")
                };
                webSocket.returnData.push(makeFakeResponse("updated"));
                MachinesManager.updateInterface(
                    machine, interface_id, params).then(
                        function() {
                            var sentObject = angular.fromJson(
                                webSocket.sentData[0]);
                            expect(sentObject.method).toBe(
                                "machine.update_interface");
                            expect(sentObject.params.system_id).toBe(
                                machine.system_id);
                            expect(sentObject.params.interface_id).toBe(
                                interface_id);
                            expect(sentObject.params.name).toBe(params.name);
                            done();
                        });
            });
    });

    describe("deleteInterface", function() {

        it("calls machine.delete_interface with correct params",
            function(done) {
                var machine = makemachine(), interface_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("deleted"));
                MachinesManager.deleteInterface(machine, interface_id).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.delete_interface");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.interface_id).toBe(
                            interface_id);
                        done();
                    });
            });
    });

    describe("linkSubnet", function() {

        it("calls machine.link_subnet with system_id and interface_id",
            function(done) {
                var machine = makemachine(), interface_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("updated"));
                MachinesManager.linkSubnet(machine, interface_id).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.link_subnet");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.interface_id).toBe(
                            interface_id);
                        done();
                    });
            });

        it("calls machine.link_subnet with params",
            function(done) {
                var machine = makemachine(), interface_id = makeInteger(0, 100);
                var params = {
                    name: makeName("eth0")
                };
                webSocket.returnData.push(makeFakeResponse("updated"));
                MachinesManager.linkSubnet(machine, interface_id, params).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.link_subnet");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.interface_id).toBe(
                            interface_id);
                        expect(sentObject.params.name).toBe(params.name);
                        done();
                    });
            });
    });

    describe("unlinkSubnet", function() {

        it("calls machine.unlink_subnet with correct params",
            function(done) {
                var machine = makemachine(), interface_id = makeInteger(0, 100);
                var link_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("updated"));
                MachinesManager.unlinkSubnet(
                    machine, interface_id, link_id).then(
                        function() {
                            var sentObject = angular.fromJson(
                                webSocket.sentData[0]);
                            expect(sentObject.method).toBe(
                                "machine.unlink_subnet");
                            expect(sentObject.params.system_id).toBe(
                                machine.system_id);
                            expect(sentObject.params.interface_id).toBe(
                                interface_id);
                            expect(sentObject.params.link_id).toBe(
                                link_id);
                            done();
                        });
            });
    });

    describe("updateFilesystem", function() {
        it("calls machine.update_filesystem", function(done) {
            var fakemachine = makemachine();
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.updateFilesystem(
                    fakemachine.system_id, makeName("block_id"),
                    makeName("partition_id"), makeName("fstype"),
                    makeName("mount_point")).then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.update_filesystem");
                done();
            });
        });

        it("calls machine.update_filesystem with params", function(done) {
            var fakemachine = makemachine();
            var block_id = makeName("block_id");
            var partition_id = makeName("partition_id");
            var fstype = makeName("fstype");
            var mount_point = makeName("mount_point");
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.updateFilesystem(
                    fakemachine, block_id, partition_id,
                    fstype, mount_point).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.update_filesystem");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.block_id).toBe(block_id);
                expect(sentObject.params.partition_id).toBe(partition_id);
                expect(sentObject.params.fstype).toBe(fstype);
                expect(sentObject.params.mount_point).toBe(mount_point);
                done();
            });
        });
    });

    describe("updateDiskTags", function() {

        it("calls machine.update_disk_tags", function(done) {
            var fakemachine = makemachine();
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.updateDiskTags(
                    fakemachine, makeName("block_id"),
                    [ makeName("tag") ]).then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.update_disk_tags");
                done();
            });
        });

        it("calls machine.update_disk_tags with params", function(done) {
            var fakemachine = makemachine();
            var block_id = makeName("block_id");
            var tags = [ makeName("tag") ];
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.updateDiskTags(
                    fakemachine, block_id, tags).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.update_disk_tags");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.block_id).toBe(block_id);
                expect(sentObject.params.tags[0]).toBe(tags[0]);
                done();
            });
        });
    });

    describe("deleteDisk", function() {

        it("calls machine.delete_disk with correct params",
            function(done) {
                var machine = makemachine(), block_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("deleted"));
                MachinesManager.deleteDisk(machine, block_id).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.delete_disk");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.block_id).toBe(
                            block_id);
                        done();
                    });
            });
    });

    describe("deletePartition", function() {

        it("calls machine.delete_partition with correct params",
            function(done) {
                var machine = makemachine(), partition_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("deleted"));
                MachinesManager.deletePartition(machine, partition_id).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.delete_partition");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.partition_id).toBe(
                            partition_id);
                        done();
                    });
            });
    });

    describe("deleteVolumeGroup", function() {

        it("calls machine.delete_volume_group with correct params",
            function(done) {
                var machine = makemachine();
                var volume_group_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("deleted"));
                MachinesManager.deleteVolumeGroup(
                    machine, volume_group_id).then(
                        function() {
                            var sentObject = angular.fromJson(
                                webSocket.sentData[0]);
                            expect(sentObject.method).toBe(
                                "machine.delete_volume_group");
                            expect(sentObject.params.system_id).toBe(
                                machine.system_id);
                            expect(sentObject.params.volume_group_id).toBe(
                                volume_group_id);
                            done();
                        });
            });
    });

    describe("deleteCacheSet", function() {

        it("calls machine.delete_cache_set with correct params",
            function(done) {
                var machine = makemachine(), cache_set_id = makeInteger(0, 100);
                webSocket.returnData.push(makeFakeResponse("deleted"));
                MachinesManager.deleteCacheSet(machine, cache_set_id).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.delete_cache_set");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.cache_set_id).toBe(
                            cache_set_id);
                        done();
                    });
            });
    });

    describe("createPartition", function() {

        it("calls machine.create_partition with correct params",
            function(done) {
                var machine = makemachine(), block_id = makeInteger(0, 100);
                var size = makeInteger(1024 * 1024, 1024 * 1024 * 1024);
                webSocket.returnData.push(makeFakeResponse("deleted"));
                MachinesManager.createPartition(machine, block_id, size).then(
                    function() {
                        var sentObject = angular.fromJson(
                            webSocket.sentData[0]);
                        expect(sentObject.method).toBe(
                            "machine.create_partition");
                        expect(sentObject.params.system_id).toBe(
                            machine.system_id);
                        expect(sentObject.params.block_id).toBe(
                            block_id);
                        expect(sentObject.params.partition_size).toBe(
                            size);
                        done();
                    });
            });

        it("calls machine.create_partition with extra params",
            function(done) {
                var params = { fstype: "ext4" };
                var machine = makemachine(), block_id = makeInteger(0, 100);
                var size = makeInteger(1024 * 1024, 1024 * 1024 * 1024);
                webSocket.returnData.push(makeFakeResponse("deleted"));
                MachinesManager.createPartition(
                    machine, block_id, size, params).then(
                        function() {
                            var sentObject = angular.fromJson(
                                webSocket.sentData[0]);
                            expect(sentObject.method).toBe(
                                "machine.create_partition");
                            expect(sentObject.params.system_id).toBe(
                                machine.system_id);
                            expect(sentObject.params.block_id).toBe(
                                block_id);
                            expect(sentObject.params.partition_size).toBe(
                                size);
                            expect(sentObject.params.fstype).toBe("ext4");
                            done();
                        });
            });
    });

    describe("createCacheSet", function() {

        it("calls machine.create_cache_set", function(done) {
            var fakemachine = makemachine();
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createCacheSet(
                    fakemachine, "", "").then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_cache_set");
                done();
            });
        });

        it("calls machine.create_cache_set with params", function(done) {
            var fakemachine = makemachine();
            var block_id = makeName("block_id");
            var partition_id = makeName("block_id");
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createCacheSet(
                    fakemachine, block_id, partition_id).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_cache_set");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.block_id).toBe(block_id);
                expect(sentObject.params.partition_id).toBe(partition_id);
                done();
            });
        });
    });

    describe("createBcache", function() {

        it("calls machine.create_bcache", function(done) {
            var fakemachine = makemachine();
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createBcache(
                    fakemachine, {}).then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_bcache");
                done();
            });
        });

        it("calls machine.create_bcache with params", function(done) {
            var fakemachine = makemachine();
            var params = {
                block_id: makeName("block_id"),
                partition_id: makeName("block_id")
            };
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createBcache(
                    fakemachine, params).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_bcache");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.block_id).toBe(params.block_id);
                expect(sentObject.params.partition_id).toBe(
                    params.partition_id);
                done();
            });
        });
    });

    describe("createRAID", function() {

        it("calls machine.create_raid", function(done) {
            var fakemachine = makemachine();
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createRAID(
                    fakemachine, {}).then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_raid");
                done();
            });
        });

        it("calls machine.create_raid with params", function(done) {
            var fakemachine = makemachine();
            var params = {
                block_id: makeName("block_id"),
                partition_id: makeName("block_id")
            };
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createRAID(
                    fakemachine, params).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_raid");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.block_id).toBe(params.block_id);
                expect(sentObject.params.partition_id).toBe(
                    params.partition_id);
                done();
            });
        });
    });

    describe("createVolumeGroup", function() {

        it("calls machine.create_volume_group", function(done) {
            var fakemachine = makemachine();
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createVolumeGroup(
                    fakemachine, {}).then(function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_volume_group");
                done();
            });
        });

        it("calls machine.create_volume_group with params", function(done) {
            var fakemachine = makemachine();
            var params = {
                block_id: makeName("block_id"),
                partition_id: makeName("block_id")
            };
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createVolumeGroup(
                    fakemachine, params).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_volume_group");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.block_id).toBe(params.block_id);
                expect(sentObject.params.partition_id).toBe(
                    params.partition_id);
                done();
            });
        });
    });

    describe("createLogicalVolume", function() {

        it("calls machine.create_logical_volume", function(done) {
            var fakemachine = makemachine();
            var volume_group_id = makeInteger(0, 100);
            var name = makeName("lv");
            var size = makeInteger(1000 * 1000, 1000 * 1000 * 1000);
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createLogicalVolume(
                    fakemachine, volume_group_id, name, size).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_logical_volume");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.volume_group_id).toBe(
                    volume_group_id);
                expect(sentObject.params.name).toBe(name);
                expect(sentObject.params.size).toBe(size);
                done();
            });
        });

        it("calls machine.create_logical_volume with extra", function(done) {
            var fakemachine = makemachine();
            var volume_group_id = makeInteger(0, 100);
            var name = makeName("lv");
            var size = makeInteger(1000 * 1000, 1000 * 1000 * 1000);
            var extra = { fstype: "ext4" };
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.createLogicalVolume(
                    fakemachine, volume_group_id, name, size, extra).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.create_logical_volume");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.volume_group_id).toBe(
                    volume_group_id);
                expect(sentObject.params.name).toBe(name);
                expect(sentObject.params.size).toBe(size);
                expect(sentObject.params.fstype).toBe("ext4");
                done();
            });
        });
    });

    describe("setBootDisk", function() {

        it("calls machine.set_boot_disk", function(done) {
            var fakemachine = makemachine();
            var block_id = makeInteger(0, 100);
            webSocket.returnData.push(makeFakeResponse(null));
            MachinesManager.setBootDisk(
                    fakemachine, block_id).then(
                        function() {
                var sentObject = angular.fromJson(webSocket.sentData[0]);
                expect(sentObject.method).toBe("machine.set_boot_disk");
                expect(sentObject.params.system_id).toBe(fakemachine.system_id);
                expect(sentObject.params.block_id).toBe(
                    block_id);
                done();
            });
        });
    });
});
