# spotify

This tool is used to convert EC2 on-demand instances to spot instances with one click

### Prerequisites
You must configure boto (AWS python client) before hand and set your default region
#### Installation
Download the reop and install required libraries<br>
`pip install -r requirements.txt`

### Make it so
Several option to run spotify<br>
* `python spotify.py i-01jhbdihugdiygw -d`<br>
Get an estimate on the prices for on-demand (what you pay now) and spot (what you'll pay potentially)
* `python spotify.py i-01jhbdihugdiygw -k`<br>
Create a spot instance without stopping source instance. This option will require additional configuration post run
* `python spotify.py i-01jhbdihugdiygw -r`<br>
Create a spot instance and transfer some additional parameters to the new instance (e.g. EIP, Security group, IAM role)<br>
Instance will remain stopped after the conversion
