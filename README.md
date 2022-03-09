# Inception

(Work in progress)

A simple benchmark to measure the cost of nested virtualization. A virtual
machine is created and launched, which then creates and launches a virtual
machine, and so on to a given depth of nesting. The time to build the
virtual machine at each level can then be used as a crude estimate for
the penalty of virtualization with that degree of nesting.

## Prerequisits:

This test requires qemu installed with kvm support and the libguestfs tools.
These can be installed on a Debian-based distribution using:

    sudo apt install -y libguestfs-tools qemu-system-x86

## Usage:

(likely to change):

    ./inception.py config.json results.csv
