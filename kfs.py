#!/usr/bin/env python

import sys
import argparse
from lib.createCluster import create_cluster
from lib.addNode import add_nodes
from lib.resetNode import reset_node_from_cluster
from lib.upgradeCluster import upgrade_cluster
from lib.upgradeKubeVip import upgrade_kube_vip
from lib.upgradeCalico import upgrade_calico
from lib.showClusterState import show_cluster_state
from argparse import RawTextHelpFormatter


class KfsArgParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help(sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    parser = KfsArgParser(description='KFS - Kubernetes Fucking Simple - A tool for setting up and managing Kubernetes clusters', formatter_class=RawTextHelpFormatter)
    parser.add_argument("--cluster-file", required=True, type=str, help="Path to the cluster file.\n"
                                                                        "Example:\n"
                                                                        "    --cluster-file /path/to/cluster-file.yaml")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create-cluster", action='store_true', help="Create a cluster")
    group.add_argument("--upgrade-cluster", action='store_true', help="Upgrade the cluster to the version defined in the cluster-file")
    group.add_argument("--add-nodes", action='store_true', help="Add new nodes from the updated cluster file")
    group.add_argument("--reset-nodes", "--delete-node", action='append', help="Can be used multiple times or once with value 'ALL' to reset whole cluster.\n"
                                                                              "Example:\n"
                                                                              "    --reset-nodes node1 --reset-nodes node2 ...\n"
                                                                              "  or for whole cluster just\n"
                                                                              "    --reset-nodes ALL\n"
                                                                              "  or alternatively\n"
                                                                              "    --delete-node node1 --delete-node node2 ...\n"
                                                                              "  or for whole cluster just\n"
                                                                              "    --delete-node ALL")
    group.add_argument("--upgrade-kube-vip", action='store_true', help="Upgrades/Reinstalls Kube-Vip on all master nodes with the set version.")
    group.add_argument("--upgrade-calico", action='store_true', help="Upgrades/Reinstalls Tigera-Operator/Calico.")
    group.add_argument("--show-cluster-state", action='store_true', help="Simply show the current nodes and their state.")
    parser.add_argument("--unattended-mode", action='store_true', help="Unattended mode - ATTENTION!!! THIS MIGHT DESTROY THE SELECTED CLUSTER!!!")
    args = parser.parse_args()

    if args.unattended_mode:
        if args.create_cluster:
            create_cluster(args.cluster_file, args.unattended_mode)
        elif args.upgrade_cluster:
            upgrade_cluster(args.cluster_file, args.unattended_mode)
        elif args.add_nodes:
            add_nodes(args.cluster_file, args.unattended_mode)
        elif args.reset_nodes:
            if len(args.reset_nodes) >= 1:
                reset_node_from_cluster(args.cluster_file, args.reset_nodes, args.unattended_mode)
            else:
                print("Either give a list of nodes or use ALL for resetting a whole cluster")
            # endif
        elif args.upgrade_kube_vip:
            upgrade_kube_vip(args.cluster_file, args.unattended_mode)
        elif args.upgrade_calico:
            upgrade_calico(args.cluster_file, args.unattended_mode)
        elif args.show_cluster_state:
            show_cluster_state(args.cluster_file, args.unattended_mode)
        else:
            print("Missing arguments")
            parser.print_help(sys.stderr)
            sys.exit(2)
        # endif
    else:
        if args.create_cluster:
            create_cluster(args.cluster_file)
        elif args.upgrade_cluster:
            upgrade_cluster(args.cluster_file)
        elif args.add_nodes:
            add_nodes(args.cluster_file)
        elif args.reset_nodes:
            if len(args.reset_nodes) >= 1:
                reset_node_from_cluster(args.cluster_file, args.reset_nodes)
            else:
                print("Either give a list of nodes or use ALL for resetting a whole cluster")
            # endif
        elif args.upgrade_kube_vip:
            upgrade_kube_vip(args.cluster_file)
        elif args.upgrade_calico:
            upgrade_calico(args.cluster_file)
        elif args.show_cluster_state:
            show_cluster_state(args.cluster_file)
        else:
            print("Missing arguments")
            parser.print_help(sys.stderr)
            sys.exit(2)
        # endif
    # endif
# endif (main)
