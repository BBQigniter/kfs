# Some Test-Scenarios

* start creating a cluster with for example 1.28.x
  * one master and one worker
    * you might have to downgrade packages first on reset nodes if they had a higher version installed previously. 
      Can be done locally on the node for example via: 
      ```
      dnf downgrade --disableexcludes=all kubeadm-1.28.13 kubelet-1.28.13 kubectl-1.28.13 cri-tools kubernetes-cni --disablerepo '*kubernetes_*' --enablerepo '*kubernetes_v1_28*'
      ```
* test upgrades to newer versions (Kubernetes, kube-vip, calico)
* add node in between
  * without extra kubelet-configuration settings
* change init-master in config
* upgrade cluster again and add/change kubelet-configuration settings for a node
* remove a node (master/worker)
* readd a node