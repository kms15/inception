#!/usr/bin/env python3
import os
import os.path
import sys
import time
import json
import tempfile
import socket

default_config = {
    'max_nesting': 4,
    'current_nesting': 0,
    'base_mem_M': 4096,
    'hosting_mem_M': 2048,
    'base_disk_M': 32768,
    'hosting_disk_M': 8192,
    'template' : 'debian-11',
    'vcpus' : 2,
    }

if __name__ == '__main__':
    start_ns = time.monotonic_ns()

    # parse the arguments
    if len(sys.argv) != 3:
        print(f'usage: \n{sys.argv[0]} <config_file.json> <results_file.csv>\n')
        sys.exit(1)

    config_filename = sys.argv[1]
    results_filename = sys.argv[2]
    prexisting_result_file = os.path.exists(results_filename)

    with (
        tempfile.NamedTemporaryFile(mode='w',suffix='.json') as guest_config_file,
        tempfile.NamedTemporaryFile(suffix='.qcow2') as guest_preresize_image,
        tempfile.NamedTemporaryFile(suffix='.qcow2') as guest_image,
        tempfile.TemporaryDirectory() as guest_results_dir,
        open(results_filename, 'a') as results_file
        ):

        # read the configuration
        config = default_config.copy()
        with open(config_filename) as f:
            config_file_settings = json.load(f)
            config.update(config_file_settings)
        print("config settings: \n", json.dumps(config, indent=4))

        # generate the guest config
        guest_config = config.copy()
        guest_config['current_nesting'] += 1
        json.dump(guest_config, fp=guest_config_file, indent=4)
        guest_config_file.flush()

        # calculate the memory and disk size of the guest:
        nesting_remaining = max(0, config['max_nesting'] - guest_config['current_nesting'])
        guest_mem_M = config['base_mem_M'] + nesting_remaining * config['hosting_mem_M']
        guest_disk_M = config['base_disk_M'] + nesting_remaining * config['hosting_disk_M']

        # add a header to the results file
        if not prexisting_result_file:
            results_file.write('depth,measure,value\n')

        finished_startup_ns = time.monotonic_ns()
        results_file.write(f"{config['current_nesting']},startup," +
            f"{ (finished_startup_ns - start_ns)/1e9 }\n")

        # wait for the network to come up
        max_network_retries = 60
        retry = 0
        while True:
            try:
                socket.gethostbyname('builder.libguestfs.org')
                break
            except socket.gaierror:
                if retry == max_network_retries:
                    print(f'Timeout while waiting for network!')
                    results_file.write(f"{config['current_nesting']},networktimeout," +
                        f"{ max_network_retries }\n")
                    sys.exit(1)
                elif retry % 5 == 0:
                    print(f'Waiting for network ({retry}/{max_network_retries})...')
                time.sleep(1)
            retry += 1
        network_up_ns = time.monotonic_ns()
        results_file.write(f"{config['current_nesting']},waitingfornet," +
            f"{ (network_up_ns - finished_startup_ns)/1e9 }\n")

        # if we're not too deep, start a guest vm
        if config['current_nesting'] < config['max_nesting']:
            # build the guest disk image
            virtbuilder_command = (f"virt-builder " +
                f"{config['template']} --no-cache " +
                f"--format qcow2 --output {guest_preresize_image.name} " +
                "--root-password password:debug " +
                "--edit '/etc/network/interfaces: s/ens2/eth0/' " +
                "--edit '/etc/default/grub: s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"console=ttyS0 net.ifnames=0 biosdevname=0\"/' " +
                "--run-command '/usr/sbin/update-grub2' " +
                "--update --install libguestfs-tools,qemu-system-x86 " +
                f"--upload {guest_config_file.name}:/root/inception_config.json " +
                f"--upload {sys.argv[0]}:/root/inception.py " +
                f"--firstboot-command '/root/inception.py /root/inception_config.json /root/inception_results.csv' " +
                "--firstboot-command 'shutdown -h now' " +
                ""
                )
            print (virtbuilder_command)
            assert 0 == os.system(virtbuilder_command)
            finished_virtbuilder_ns = time.monotonic_ns()
            results_file.write(f"{config['current_nesting']},virtbuilder," +
                f"{ (finished_virtbuilder_ns - network_up_ns)/1e9 }\n")

            # resize the disk image
            resize_command = (f"qemu-img create -f qcow2 {guest_image.name} {guest_disk_M}M && " +
                f"virt-resize --expand /dev/sda1 {guest_preresize_image.name} {guest_image.name} && "
                f"echo 0 > {guest_preresize_image.name}")
            print (resize_command)
            assert 0 == os.system(resize_command)
            finished_resize_ns = time.monotonic_ns()
            results_file.write(f"{config['current_nesting']},resize," +
                f"{ (finished_resize_ns - finished_virtbuilder_ns)/1e9 }\n")

            # launch the guest
            qemu_command = (f"qemu-system-x86_64 -hda {guest_image.name} " +
                f"--netdev user,id=n0,net=10.0.{config['current_nesting'] + 3}.0/24 " +
                "--device virtio-net-pci,netdev=n0 " +
                f"-m {guest_mem_M}M -smp {config['vcpus']} " +
                "-machine type=pc,accel=kvm -cpu host -nographic")
            print (qemu_command)
            assert 0 == os.system(qemu_command)
            finished_qemu_ns = time.monotonic_ns()
            results_file.write(f"{config['current_nesting']},qemu," +
                f"{ (finished_qemu_ns - finished_resize_ns)/1e9 }\n")

            # extract the results file from the guest image
            copyout_command = (f"virt-copy-out -a {guest_image.name} " +
                "/root/inception_results.csv " +
                f"{guest_results_dir}")
            print (copyout_command)
            assert 0 == os.system(copyout_command)
            with open(f"{guest_results_dir}/inception_results.csv") as guest_results:
                guest_results.readline() # skip the header
                results_file.writelines(guest_results.readlines()) # append the guest results
            finished_copyout_ns = time.monotonic_ns()
            results_file.write(f"{config['current_nesting']},copyout," +
                f"{ (finished_copyout_ns - finished_qemu_ns)/1e9 }\n")

        print(f'Total time: { (time.monotonic_ns() - start_ns)/1e9 } s')

