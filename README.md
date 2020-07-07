# spotify

This tool is used to convert EC2 on-demand instances to spot instances with one click

### Prerequisites
You must configure boto (AWS python client) before hand and set your default region
#### Installation
Download the reop and install required libraries<br>
`pip install -r requirements.txt`

### Make it so
Several options to run spotify

* Create a spot instance from an on-demand instance, regardless of any roles, security groups or subnets
`python spotify.py i-01jhbdihugdiygw`
* Create a spot instance without stopping source instance. This option will require additional configuration post run
`python spotify.py i-01jhbdihugdiygw -k`
Create a spot instance and transfer some additional parameters to the new instance (e.g. EIP, Security group, IAM role)
`python spotify.py i-01jhbdihugdiygw -r`

** Original Instance will remain stopped after the conversion**


#### Dry run - what can you save
* Get an estimate for on-demand cost and spot cost for instance
```
$ python3 spotify.py i-08ccff8f5a4ac85d6 -d
Current instance type: t2.microv
On-demand   price: 0.0146000000
Spot        price: 0.003800
```
* Get an estimate for the whole region
```
$ python3 spotify.py region -d

             Hourly cost of EC2
+---------------+----------------+----------------------+
| Instance Name | On demand cost |      spot cost       |
+---------------+----------------+----------------------+
|   inst-assi   |  0.0146000000  |       0.003800       |
|    nameless   |  0.0552000000  |       0.015000       |
|    Openvpn    |  0.0146000000  |       0.003800       |
|  ------------ |  ------------  |     ------------     |
|  Region cost  |     0.0844     | 0.022600000000000002 |
+---------------+----------------+----------------------+
```