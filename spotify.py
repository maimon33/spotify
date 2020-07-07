#!/usr/bin/env python

import os
import sys
import json
import time

import boto3
import click

from prettytable import PrettyTable
from pkg_resources import resource_filename
from botocore.exceptions import ClientError, NoRegionError

DEFAULT_REGION="eu-west-1"

def _format_json(dictionary):
    return json.dumps(dictionary, indent=4, sort_keys=True)

def instance_dict(instanceid):
    my_instance_dict = {}
    security_groups = []
    my_instance = instanceid["Reservations"][0]["Instances"][0]
    try:
        for tag in my_instance["Tags"]:
            if tag["Key"] == "Name":
                my_instance_dict["name"] = tag["Value"]
    except KeyError:
        my_instance_dict["name"] = "nameless"
    my_instance_dict["EIP"] = my_instance.get("PublicIpAddress", "stopped")
    my_instance_dict["role"] = my_instance.get("IamInstanceProfile")
    for group in my_instance["SecurityGroups"]:
        security_groups.append(group["GroupId"])
    my_instance_dict["groups"] = security_groups
    my_instance_dict["type"] = my_instance["InstanceType"]
    my_instance_dict["keypair"] = my_instance["KeyName"]
    my_instance_dict["subnet"] = my_instance["SubnetId"]
    my_instance_dict["vpc"] = my_instance["VpcId"]
    return my_instance_dict

def aws_client(region, resource=True, aws_service="ec2"):
    try:
        if resource:
            return boto3.resource(aws_service, region_name=region)
        else:
            return boto3.client(aws_service, region_name=region)
    except:
        print("client failed")
    # except NoRegionError as e:
    #     logger.warning("Error reading 'Region'. Make sure boto is configured")
    #     sys.exit()
    # except ClientError as e:
    #     print(e)

def get_role_name(region, role_arn):
    roles = aws_client(region, resource=False, aws_service="iam").list_roles()["Roles"]
    for role in roles:
        if role["RoleId"] == role_arn:
            return role["RoleName"]

def get_instance(region, instanceid):
    try:
        return aws_client(region, resource=False).describe_instances(InstanceIds=[instanceid])
    except:
        return False

def stop_instance(region, instanceid):
    aws_client(region, resource=False).stop_instances(InstanceIds=[instanceid])
    instance_waiter = aws_client(region, resource=False).get_waiter('instance_stopped')
    instance_waiter.wait(InstanceIds=[instanceid])

def create_ami(region, instanceid, instance_name, no_reboot=True):
    print("Creating AMI from on-demand instance")
    try:
        ami = aws_client(region, resource=False).create_image(Name="spotify {}".format(instance_name), InstanceId=instanceid, NoReboot=no_reboot)
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidAMIName.Duplicate':
            print("Image Name exist")
    
    spotify_image = aws_client(region, resource=False).describe_images(Filters=[{
        'Name': 'name',
        'Values': ["spotify {}".format(instance_name)]},
        ])
      
    spotify_image_id = spotify_image["Images"][0]["ImageId"]
    ami_waiter = aws_client(region, resource=False).get_waiter('image_available')
    
    ami_waiter.wait(ImageIds=[spotify_image_id],
                    WaiterConfig={
                        'Delay': 30,
                        'MaxAttempts': 100
                        })
    return spotify_image_id

def get_region_name(region_code):
    default_region = 'EU (Ireland)'
    endpoint_file = resource_filename('botocore', 'data/endpoints.json')
    try:
        with open(endpoint_file, 'r') as f:
            data = json.load(f)
        return data['partitions'][0]['regions'][region_code]['description']
    except IOError:
        return default_region

def get_instance_os(region, instanceid):
    instance = aws_client(
        resource=False,
        region=region).describe_instances(Filters=[{'Name': 'instance-id', 'Values': [instanceid]}])
    instance_ami = instance["Reservations"][0]["Instances"][0]["ImageId"]
    ami_os = aws_client(
        resource=False,
        region=region).describe_images(Filters=[{'Name': 'image-id', 'Values': [instance_ami]}])
    return ami_os["Images"][0]["PlatformDetails"].split("/")[0]

def get_instances(region):
    all_ids = []
    instances = aws_client(
        resource=False,
        region=region).describe_instances()
    all_instances = instances["Reservations"]
    for instance in all_instances:
        all_ids.append(instance["Instances"][0]["InstanceId"])
    return all_ids

def get_price(region, instance, os):
    FLT = '[{{"Field": "tenancy", "Value": "shared", "Type": "TERM_MATCH"}},'\
      '{{"Field": "operatingSystem", "Value": "{o}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "preInstalledSw", "Value": "NA", "Type": "TERM_MATCH"}},'\
      '{{"Field": "instanceType", "Value": "{t}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "locationType", "Value": "AWS Region", "Type": "TERM_MATCH"}},'\
      '{{"Field": "capacitystatus", "Value": "Used", "Type": "TERM_MATCH"}}]'

    f = FLT.format(t=instance, o=os)
    data = aws_client(
        resource=False, 
        aws_service='pricing', 
        region='us-east-1').get_products(
            ServiceCode='AmazonEC2', Filters=json.loads(f))
    od = json.loads(data['PriceList'][0])['terms']['OnDemand']
    id1 = list(od)[0]
    id2 = list(od[id1]['priceDimensions'])[0]
    return od[id1]['priceDimensions'][id2]['pricePerUnit']['USD']

def get_spot_info(region, spotid):
    client = aws_client(region, resource=False)
    spot_status = client.describe_spot_instance_requests(SpotInstanceRequestIds=[spotid])
    return spot_status["SpotInstanceRequests"][0]

def get_spot_price(region, type):
    client = aws_client(region, resource=False)
    return client.describe_spot_price_history(InstanceTypes=[type],
                                              MaxResults=1,
                                              ProductDescriptions=['Linux/UNIX (Amazon VPC)'])["SpotPriceHistory"][0]["SpotPrice"]        
        
def check_spot_status(region, client, SpotId):
    status_code = get_spot_info(region, SpotId)["Status"]["Code"]
    while status_code != "fulfilled":
        status_code = get_spot_info(region, SpotId)["Status"]["Code"]
        status_msg = get_spot_info(region, SpotId)["Status"]["Message"]
        if status_code == 'capacity-not-available' or status_code == 'pending-fulfillment' or status_code == 'fulfilled':
            print('{0}...'.format(status_code))
            time.sleep(1)
        else:
            print("{0}\n{1}".format(status_code, status_msg))
            print("cancel spot request- {0}".format(SpotId))
            client.cancel_spot_instance_requests(SpotInstanceRequestIds=[SpotId])
            sys.exit(0)

def transfer_eip(region, instanceid, spot_instance):
    eip_address = aws_client(region, resource=False).describe_addresses
    for address in eip_address()["Addresses"]:
        try:
            if address["InstanceId"] == instanceid:
                target_eip = address["AllocationId"]
        except KeyError:
            return
            pass
    try:
        response = aws_client(region, resource=False).associate_address(
            AllocationId=target_eip,
            InstanceId=spot_instance)
    except ClientError as e:
        print(e)
    
def create_spot_instance(region, instanceid, reserve, vpc, instance_name, keep_up, type, keypair, groups, transfer_ip, role, subnet):
    LaunchSpecifications = {}
    
    if role:
        LaunchSpecifications["IamInstanceProfile"] = {"Arn": role["Arn"],
                                                      "Name": get_role_name(region, role["Id"])}
    if reserve:
        LaunchSpecifications["SecurityGroupIds"] = [unicode(groups[0])]
        LaunchSpecifications["SubnetId"] = subnet
    
    client = aws_client(region, resource=False)
    
    spot_offer_price = get_spot_price(region, type)

    LaunchSpecifications["ImageId"] = create_ami(region, instanceid, instance_name, no_reboot=keep_up)
    LaunchSpecifications["InstanceType"] = type
    LaunchSpecifications["KeyName"] = keypair
    LaunchSpecifications["Placement"] = {"AvailabilityZone": ""}

    spot_instance = client.request_spot_instances(
        SpotPrice=spot_offer_price,
        Type="persistent",
        InstanceCount=1,
        InstanceInterruptionBehavior="stop",
        LaunchSpecification=LaunchSpecifications)
    SpotId = spot_instance["SpotInstanceRequests"][0]["SpotInstanceRequestId"]

    check_spot_status(region, client, SpotId)
    aws_client(region, resource=False).create_tags(Resources=[SpotId], Tags=[{'Key': 'Name', 'Value': 'Spotified instance: {}'.format(instance_name)}])
    instance = aws_client(region).Instance(id=get_spot_info(region, SpotId)["InstanceId"])
    
    instance.wait_until_running()
    instance.load()
    instance.create_tags(Tags=[{'Key': 'Name', 'Value': 'spot - {}'.format(instance_name)},
                               {'Key': 'Source Instance', 'Value': '{} ({})'.format(instanceid, instance_name)}])
    
    if transfer_ip:
        transfer_eip(region, instanceid, instance.instance_id)
    
    return instance.public_dns_name


CLICK_CONTEXT_SETTINGS = dict(
    help_option_names=['-h', '--help'],
    token_normalize_func=lambda param: param.lower(),
    ignore_unknown_options=True)

@click.command(context_settings=CLICK_CONTEXT_SETTINGS)
@click.option('--region',
              default=DEFAULT_REGION,
              help='which region to operate on?')
@click.option('-r',
              '--reserve',
              is_flag=True,
              help='Keep additional properties of the instance?')
@click.option('-k',
              '--keep-up',
              is_flag=True,
              help='prevent instance stop for image create?')
@click.option('-d',
              '--dry-run',
              is_flag=True,
              help="get an estimate on saving for instance type")
@click.argument('InstanceId')
def spotify(region, instanceid, dry_run, reserve, keep_up):
    """Convert EC2 on-demand instance to spot instance with one command
    """
    if not keep_up and not dry_run:
        if get_instance(region, instanceid):
            print("Stoping instance...")
            stop_instance(region, instanceid)
        else:
            print("Instance not in region")
            sys.exit()
    
    if instanceid != "region":
        instance = get_instance(region, instanceid)
        my_instance = instance_dict(instance)

    if dry_run:
        if instanceid == "region":
            all_instances = get_instances(region)
            x = PrettyTable()
            x.field_names = ["Instance Name", "On demand cost", "spot cost"]
            total_on_demand = []
            total_spot = []
            for instance in all_instances:
                an_instance = get_instance(region, instance)
                my_instance = instance_dict(an_instance)
                instance_os = get_instance_os(region, instance)
                instance_cost = get_price(get_region_name(region), my_instance["type"], instance_os)
                total_on_demand.append(float(instance_cost))
                spot_instance_cost = get_spot_price(region, my_instance["type"])
                total_spot.append(float(spot_instance_cost))

                x.add_row([my_instance["name"], instance_cost, spot_instance_cost])
            total_on_demand_cost = sum(total_on_demand)
            total_spot_cost = sum(total_spot)
            x.add_row(["-"*12, "-"*12, "-"*12])
            x.add_row(["Region cost", total_on_demand_cost, total_spot_cost])
            print("\n          Hourly cost of EC2 region: {}      ".format(get_region_name(region)))
            print(x)
            sys.exit()
        instance_os = get_instance_os(region, instanceid)
        instance_cost = get_price(get_region_name(region), my_instance["type"], instance_os)
        spot_instance_cost = get_spot_price(region, my_instance["type"])
        print("""Current instance type: {}
On-demand   price: {}
Spot        price: {}""".format(my_instance["type"], instance_cost, spot_instance_cost))
        sys.exit()

    spot_instance = create_spot_instance(region,
                                         instanceid=instanceid, 
                                         reserve=reserve,
                                         vpc=my_instance["vpc"],
                                         instance_name=my_instance["name"],
                                         keep_up=keep_up,
                                         type=my_instance["type"], 
                                         keypair=my_instance["keypair"], 
                                         groups=my_instance["groups"], 
                                         transfer_ip=reserve,
                                         role=my_instance["role"], 
                                         subnet=my_instance["subnet"])

if __name__ == "__main__":
    spotify()