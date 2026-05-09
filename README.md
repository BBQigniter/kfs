# KFS (Kubernetes F\*\*\*ing Simple)

A tool for managing "simple" Kubernetes clusters on RHEL nodes relying on default installation methods via DNF

## Features

| Task                 | via script | Rancher behavior¹ |
|----------------------|:----------:|:-----------------:|
| create cluster       |     OK     |        OK²        |
| add node             |     OK     |        OK         |
| delete/reset node    |     OK     |        OK         |
| delete/reset cluster |     OK     |       BAD³        |
| upgrade cluster      |     OK     |        OK         |

¹ In Rancher no management of nodes must be executed, Rancher is in this case only used for managing access to the different Kubernetes resources

² A previously via script created cluster can be easily imported to Rancher

³ If the cluster is imported at least one control-plane node ALWAYS must be available, it's quite complicated to recover a damaged "Rancher cluster".
So if you really want to delete all nodes from a Rancher cluster, but not the cluster itself in Rancher, make sure the last node is available, add a new master,
and then delete the previous node. Anyway this is a very strange edge case that acutally should never happen ;) - if you really want to just delete the 
cluster in Rancher, you can do it easily. A cleanup-job will be scheduled on the nodes (in namespace `default` you'll see a pod like `cattle-cleanup-klc5p-dfczw`, 
that will try to remove anything Rancher related (some stuff still might be left over). In case you want to really clean it up use the `user-cluster.sh` in the 
`additional_scripts` folder on one master node (also copy `user-cluster.yml`)- after the job ran (watch the pods while the cluster is being deleted from Rancher).
More info can be found further down.

## Prerequisites

### Python
It was checked that all Python packages needed are available from standard RHEL-repositories

```shell
dnf install python3-jinja2 python3-packaging python3-paramiko python3-ruamel-yaml python3-schema python3-toml
```

### On/To Nodes

* **SSH `root`-access** to each node via SSH-keyfile authentication
* KFS will check if `swap` is disabled, `net.ipv4.ip_forward` sysctl-parameter is set to `1` and if the following kernel modules are enabled `overlay` and `br_netfilter`
* `containerd` must be installed via the `docker-ce` RPM-repository (package-name: `containerd.io`) and/or the `docker-ce`-repo must be usable
  * `containerd` - preparations (handled automatically by `fix_containerd_config()` in `kfsTools.py`)
    * A fresh `config.toml` must be created on the nodes:
      ```shell
      mv /etc/containerd/config.toml /etc/containerd/config.toml.orig
      containerd config default > /etc/containerd/config.toml
      ```
    * KFS will set some parameters itself like:
      ```toml
      # we have to fix a few settings in the config.toml file like:
      [plugins."io.containerd.grpc.v1.cri"]
         enable_unprivileged_icmp = true
         enable_unprivileged_ports = true
         # will be updated accordingly depending on the Kubernetes version
         sandbox_image = "registry.k8s.io/pause:3.9"
    
      [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
         SystemdCgroup = true
      ```
* In case of none-default container-image repositories, you need to configure your cache-tools/proxies accordingly
* Make sure needed repositories are set up
  * the Kubernetes repo-IDs need to be "globbable" by `*kubernetes_*` and `*kubernetes_v<reformatted_kube-version>` (e.g.: `*kubernetes_v1_29*`) (handled in `kfsTools.py`) 

# Usage

The main-script is kept as simple as possible and all files needed for a node are saved in a cluster-named subdirectory with additional subdirectories for each node 
holding the files that are used for setting up a cluster. On the node the files are copied into the needed folders or just into `/root`-home, in case something goes 
wrong and manual intervention is needed. 

```shell
usage: kfs.py [-h] --cluster-file CLUSTER_FILE (--create-cluster | --upgrade-cluster | --add-nodes | --reset-nodes RESET_NODES | --upgrade-kube-vip | --upgrade-calico | --show-cluster-state) [--unattended-mode]

KFS - Kubernetes Fucking Simple - A tool for setting up and managing Kubernetes clusters

options:
  -h, --help            show this help message and exit
  --cluster-file CLUSTER_FILE
                        Path to the cluster file.
                        Example:
                            --cluster-file /path/to/cluster-file.yaml
  --create-cluster      Create a cluster
  --upgrade-cluster     Upgrade the cluster to the version defined in the cluster-file
  --add-nodes           Add new nodes from the updated cluster file
  --reset-nodes, --delete-node RESET_NODES
                        Can be used multiple times or once with value 'ALL' to reset whole cluster.
                        Example:
                            --reset-nodes node1 --reset-nodes node2 ...
                          or for whole cluster just
                            --reset-nodes ALL
                          or alternatively
                            --delete-node node1 --delete-node node2 ...
                          or for whole cluster just
                            --delete-node ALL
  --upgrade-kube-vip    Upgrades/Reinstalls Kube-Vip on all master nodes with the set version.
  --upgrade-calico      Upgrades/Reinstalls Tigera-Operator/Calico.
  --show-cluster-state  Simply show the current nodes and their state.
  --unattended-mode     Unattended mode - ATTENTION!!! THIS MIGHT DESTROY THE SELECTED CLUSTER!!!
```

You only need a cluster-file in the `./clusters`-directory with following values (read comments for more info):

```yaml
cluster:
  name: "test-cluster"
dnf-repo:
  # if server is allowed to access external repos, we can upload the repo-file if needed
  # TODO: upload not implemented yet - also most of our servers won't be able to use this
  upload: False
kube:
  version: "1.35.3"
  image:
    repository: registry.k8s.io
  api:
    # This VIP is handled via a kube-vip setup in control-plane mode
    vip: "192.168.100.77"
    port: "6443"
  service:
    subnet: "10.43.0.0/16"
  pod:
    subnet: "10.42.0.0/16"
calico:
  tigera:
    version: "v3.28.2"
    # default is actually "docker.io" for the calico-images and "quay.io" for the tigera-operator
    # the slash at the end is needed!
    registry: "quay.io/"
    # sets default blocksize for a node for podCIDR
    # in case more pods could be handled by a node, set for example 23 and edit kubec-controller
    # and kubelet start parameters
    # default 24 - allows max of about 255 pods on one node - but maxPods is default at 110 because
    # kubernetes needs a certain amount of free IPs so the scheduler can handle everything
    # if you want to handle for example 250 pods per node, set blockSize to 23
    #
    # this value will also be used for the controllerManager's "node-cird-mask-size" setting
    # TODO: add link describing this in detail
    blockSize: "24"
  kubelet:
    extraArgs:
      # default for 24 is 110 - with blocksize 23 you can set this for example to 250
      maxPods: "110"
kubeVip:
  image:
    path: "ghcr.io/kube-vip/kube-vip:v1.1.2"
nodes:
  # mark one node with init-master because this is the only one with `super-admin.conf` with version 1.29+
  - nodeName: "homelab-1.home.arpa"
    nodeIp: "192.168.100.75"
    nodeInterface: "ens192"
    # type can be init-master or init-master+worker (only defined once!!!), master, master+worker, worker
    type: "init-master"
    sshUser: "root"
    sshKey: "/home/administrator/.ssh/id_rsa"
    # nodeLables are optional
    nodeLabels:
      # some node lables for Rancher to detect correct node role. Rancher expects "controlplane" instead of "control-plane" to detect this role.
      - "node-role.kubernetes.io/controlplane=true"
      - "node-role.kubernetes.io/etcd=true"
    # kubelet can be set optionally
    kubelet:
      # set "normal" or "early" (in case you have systems with less disk-space)
      # configures "imageGCHighThresholdPercent" and "imageGCLowThresholdPercent" in the kubelet config
      # "normal" sets the default values 85 and 80
      # "early" sets tehm to 75 and 70
      # more info:
      # * https://kubernetes.io/docs/reference/config-api/kubelet-config.v1beta1/#kubelet-config-k8s-io-v1beta1-KubeletConfiguration
      # * https://kubernetes.io/docs/concepts/architecture/garbage-collection/#containers-images
      garbageCollection: "early"
  - nodeName: "homelab-2.home.arpa"
    nodeIp: "192.168.100.76"
    nodeInterface: "ens192"
    type: "worker"
    sshUser: "root"
    sshKey: "/home/administrator/.ssh/id_rsa"
    # upgradeCheckScripts can be set optionally
    # the scripts should be placed in the "additional_scripts" directory - normally only makes sense for worker nodes or if certain important things are
    # dedicatedly running on a node
    upgradeCheckScripts:
        # scriptDescription is optional
      - scriptDescription: "A script that randomly will exit with 0 and echos given parameters"
        scriptPath: "test-script/check_test_1.sh"
        # scriptParameters is optional and must be a list
        scriptParameters: 
          - "--arg1 test-parameter-1"
          - "test-parameter-2"
      - scriptDescription: "A script that randomly will exit with 0"
        scriptPath: "test-script/check_test_2.sh"
```

### kubelet parameters

#### Garbage Collection Values

> Lower values might only make sense on nodes with less disk-space available. 

As described above you can currently set `kubelet.garbageCollection` to `normal` or `early`. A file will be uploaded to the node to `/etc/kubernetes/patches` and as 
described at https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/control-plane-flags/#patches when a cluster node is created, `kubelet` will 
first download the configured cluster-wide `kubelet-config` and patch the set values if configured.

In case you want to set those subsequently to nodes of an existing cluster, you either can configure the parameters in the cluster-file and do an upgrade to 
a newer Kubernetes version - or if you do not want to upgrade yet, you can create the patch file manually and execute for example:

```
kubeadm upgrade node --patches /etc/kubernetes/patches
```

You might have to restart `kubelet` afterwards. To check the settings locally on the node, you can have a look at `/var/lib/kubelet/config.yaml`

## Create a cluster

As described, make sure the node where this script is placed has access to all configured nodes in the cluster-file. Then simply call `kfs.py` for example:

```shell
./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --create-cluster
```
> If you know what you are doing and sure that all nodes are prepared correctly you can use the `--unattended-mode`-flag. The script then does not wait for certain tasks to 
> be accepted or confirmed.

## Adding a node

First append one or multiple new node-config(s) to the cluster-file, then just execute:

```shell
./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --add-nodes
```

> In case the installed kubernetes packages are different to the cluster's version - this will fail. Make sure the needed packages are already at the same version 
> or do not install any kubernetes packages before adding a new node.

## Deleting a node

You can just remove a node from a cluster by executing: 

```shell
./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --delete-node homelab-2.home.arpa

# or

./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --reset-nodes homelab-2.home.arpa
```

The node will be removed from the cluster, `kubeadm reset` will be executed, the directories `/etc/cni/net.d` and `/etc/kubernetes` will be deleted, `kubelet`, `containerd` and  
all its containers will be stopped and disabled. **The node-config will NOT be removed from the cluster-file!**

> You also can delete multiple nodes at once by adding the parameter multiple times like `--delete-node node-1 --delete-node node-2`

> It's recommended that you DO NOT delete multiple control-plane nodes at once (tests showed that it works, but for bigger cluster setups where `etcd` has more data, it might be error-prone).

> The script refuses to reset `init-master`-nodes! If you want to remove an `init-master` move the role first to another eligible node in the cluster-file.

### Deleting whole cluster

Make sure that you are sure :)

```shell
./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --reset-nodes ALL

# or

./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --delete-node ALL
```

### Upgrading a cluster

Edit the `kube.version` value in the cluster-file and execute

```shell
./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --upgrade-cluster
```

The script will check if the upgrade to the selected version (minor version must not be skipped!) is viable and will keep to the default workflow found in the Kubernetes' documentation by upgrading the defined init-master first, continuing with other master-nodes and finally the worker-nodes.

> The unattended flag will be only ignored for the `kubeadm upgrade plan` command currently because there is no nice way at the moment to check the case if there are manual changes needed.

The rough workflow looks like:

* upgrade only `kubeadm`-package on first control-plane node and execute the needed `kubeadm`-commands
* repeat on all other control-plane node with slightly different `kubeadm`-command
* upgrade all other Kubernetes related packages on the control-plane nodes
  * by draining node
  * upgrading containerd if applicable
  * upgrade other packages
  * restart services
  * uncordon node
* upgrade worker nodes
  * drain node
  * upgrade packages
  * execute `kubeadm` command
  * restart services
  * uncordon node

### Upgrading/Reinstalling Kube-Vip

Edit the `kubeVip.image.path` accordingly and just call the script:

```shell
./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --upgrade-kube-vip
```

> ATTENTION! Always read the changelogs before using a new Kube-Vip version, you might have to update the template
 
### Upgrading/Reinstalling Calico

Edit the `calico.tigera.version` accordingly and just call the script:

```shell
./kfs.py --cluster-file /home/administrator/PycharmProjects/kfs/clusters/test-cluster.yaml --unattended-mode --upgrade-calico
```

> ATTENTION! Always read the changelogs before using a new Calico version, you might have to update the template

# Additional Scripts

## test-scripts

Example scripts that randomly exit with 0 after a certain number is hit. 

Such scripts can be useful to wait for certain workloads running on nodes that might take a while to come back online and healthy after a node was upgraded. 

Those scripts can be in any language, exit with code >= 1 (FAIL) or 0 (OK) and they must be executable.

## Rancher

Here you find a script for cleaning nodes from mostly all Rancher related stuff.

> ATTENTION! There are some general quirks. If you do not delete nodes first, and they still show in Rancher's cluster-management, this may break your setup and a node with 
> the same name might not be able to join a cluster again in Rancher
 
Copy it to the node you want to clean up and execute:

```shell
# ./user-cluster.sh rancher/rancher-agent:<VERSION_THAT_WAS_USED>
# example
./user-cluster.sh rancher/rancher-agent:v2.8.5
```

> **DO NOT DELETE ANY RANCHER RELATED STUFF MANUALLY** - IT MAY RENDER YOUR CLUSTER UNMANAGEABLE, because it digs in very deep into Kubernetes!

# Known Issues

## Single Node Cluster Upgrade

Draining single node clusters is disabled.

## CoreDNS Pods Not Starting After Upgrade

This might have to do with changes in CoreDNS - those pods are not running as root since some version.

There are currently a few github-issues like https://github.com/kubernetes/kubernetes/issues/125226

One solution is to change the deployment-manifest slightly - like:

```
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    k8s-app: kube-dns
  name: coredns
  namespace: kube-system
spec:
  progressDeadlineSeconds: 600
  replicas: 2
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      k8s-app: kube-dns
  strategy:
    rollingUpdate:
      maxSurge: 25%
      maxUnavailable: 1
    type: RollingUpdate
  template:
    metadata:
      creationTimestamp: null
      labels:
        k8s-app: kube-dns
    spec:
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
          - podAffinityTerm:
              labelSelector:
                matchExpressions:
                - key: k8s-app
                  operator: In
                  values:
                  - kube-dns
              topologyKey: kubernetes.io/hostname
            weight: 100
      containers:
      - args:
        - -conf
        - /etc/coredns/Corefile
        image: registry.k8s.io/coredns/coredns:v1.11.1
        imagePullPolicy: IfNotPresent
        livenessProbe:
          failureThreshold: 5
          httpGet:
            path: /health
            port: 8080
            scheme: HTTP
          initialDelaySeconds: 60
          periodSeconds: 10
          successThreshold: 1
          timeoutSeconds: 5
        name: coredns
        ports:
        - containerPort: 53
          name: dns
          protocol: UDP
        - containerPort: 53
          name: dns-tcp
          protocol: TCP
        - containerPort: 9153
          name: metrics
          protocol: TCP
        readinessProbe:
          failureThreshold: 3
          httpGet:
            path: /ready
            port: 8181
            scheme: HTTP
          periodSeconds: 10
          successThreshold: 1
          timeoutSeconds: 1
        resources:
          limits:
            memory: 170Mi
          requests:
            cpu: 100m
            memory: 70Mi
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop:
            - ALL
          readOnlyRootFilesystem: true
        terminationMessagePath: /dev/termination-log
        terminationMessagePolicy: File
        volumeMounts:
        - mountPath: /etc/coredns
          name: config-volume
          readOnly: true
      dnsPolicy: Default
      nodeSelector:
        kubernetes.io/os: linux
      priorityClassName: system-cluster-critical
      restartPolicy: Always
      schedulerName: default-scheduler
      securityContext:
        sysctls:
        - name: net.ipv4.ip_unprivileged_port_start
          value: "53"
      serviceAccount: coredns
      serviceAccountName: coredns
      terminationGracePeriodSeconds: 30
      tolerations:
      - key: CriticalAddonsOnly
        operator: Exists
      - effect: NoSchedule
        key: node-role.kubernetes.io/control-plane
      volumes:
      - configMap:
          defaultMode: 420
          items:
          - key: Corefile
            path: Corefile
          name: coredns
        name: config-volume
```

Another solution is probably to set `enable_unprivileged_ports` to `true` in `/etc/containerd/config.toml` on all nodes - see https://github.com/containerd/containerd/issues/4936 and https://github.com/containerd/containerd/pull/6170/files

> This was fixed in KFS - the discussed parameters are now set when creating a cluster - with incoming future release of containerd v2, these settings will be default anyway it seems. 

## Upgrade fails with message "[upgrade/config] FATAL: failed to get node registration: node NODENAME doesn't have kubeadm.alpha.kubernetes.io/cri-socket annotation"

I only ran once into this issue and still more investigation is needed why this annotation was missing - either Rancher did something to the nodes, when the cluster was imported. Or the cluster-cleanup script supplied by Rancher.

A fix was to just add the annotation to the nodes of the affected cluster via

```
kubectl annotate node homelab-1.home.arpa kubeadm.alpha.kubernetes.io/cri-socket=unix:///var/run/containerd/containerd.sock
kubectl annotate node homelab-2.home.arpa kubeadm.alpha.kubernetes.io/cri-socket=unix:///var/run/containerd/containerd.sock
```

## Setup of cluster fails with "FATA[0000] getting status of runtime: unmarshal status info JSON: json: cannot unmarshal string into Go value of type map[string]interface {}"

Check if `cri-tools` v1.31.0 is installed - this version has a bug. Details at https://github.com/kubernetes-sigs/cri-tools/issues/1566

## After resetting a cluster

### I want to downgrade packages to a certain version

Use similar command for example if you want to roll back to v1.28.13, the additional packages `cri-tools` and `kubernetes-cni` also should be installed from the same repo like `kubeadm`

```
dnf downgrade --disableexcludes=all kubeadm-1.28.13 kubelet-1.28.13 kubectl-1.28.13 cri-tools kubernetes-cni --disablerepo '*kubernetes*' --enablerepo '*kubernetes_v1_28*'
```

# Known-Issues/TODOs

* Add option to repeat package-installation in Kubernetes upgrade-situations to manually fix troubles before trying continuing. Else the whole upgrade-procedure is broken and a user has to do it manually for other nodes.
* Add check that there is only one single init-master defined per cluster
* Better logging - e.g. like writing output automatically to a log-file
* Make the uploading of the patches-file(s) prettier - currently is ugly
* Make the upload-file function prettier - the part how the needed folders are created is currently very ugly
* Calico upgrade procedure still needs some improvements, like checking version if it really is an upgrade,...

