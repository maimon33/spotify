#!/usr/bin/env python

import os
import sys
import json
import time

import boto3
import click

from botocore.exceptions import ClientError, NoRegionError
from pkg_resources import resource_filename

DEFAULT_REGION="eu-west-1"

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
    except NoRegionError as e:
        logger.warning("Error reading 'Default Region'. Make sure boto is configured")
        sys.exit()

def get_role_name(region, role_arn):
    roles = aws_client(region, resource=False, aws_service="iam").list_roles()["Roles"]
    for role in roles:
        if role["Arn"] == role_arn:
            return role["RoleName"]

def get_instance(region, instanceid):
    return aws_client(region, resource=False).describe_instances(InstanceIds=[instanceid])

def stop_instance(region, instanceid):
    aws_client(region, resource=False).stop_instances(InstanceIds=[instanceid])
    instance_waiter = aws_client(region, resource=False).get_waiter('instance_stopped')
    instance_waiter.wait(InstanceIds=[instanceid])

def create_ami(region, instanceid, instance_name, no_reboot=True):
    try:
        ami = aws_client(region, resource=False).create_image(Name="spotify {}".format(instance_name), InstanceId=instanceid, NoReboot=no_reboot)
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidAMIName.Duplicate':
            print "Image Name exist"
    
    spotify_image = aws_client(region, resource=False).describe_images(Filters=[{
        'Name': 'name',
        'Values': ['spotify-ami',]},
        ])
    
    spotify_image_id = spotify_image["Images"][0]["ImageId"]
    ami_waiter = aws_client(region, resource=False).get_waiter('image_available')
    
    ami_waiter.wait(ImageIds=[spotify_image_id],
                    WaiterConfig={
                        'Delay': 5,
                        'MaxAttempts': 50
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

def get_price(region, instance, os):
    FLT = '[{{"Field": "tenancy", "Value": "shared", "Type": "TERM_MATCH"}},'\
      '{{"Field": "operatingSystem", "Value": "{o}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "preInstalledSw", "Value": "NA", "Type": "TERM_MATCH"}},'\
      '{{"Field": "instanceType", "Value": "{t}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "location", "Value": "{r}", "Type": "TERM_MATCH"}}]'

    f = FLT.format(r=region, t=instance, o=os)
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
            print '{0}...'.format(status_code)
            time.sleep(1)
        else:
            print "{0}\n{1}".format(status_code, status_msg)
            print "cancel spot request- {0}".format(SpotId)
            client.cancel_spot_instance_requests(SpotInstanceRequestIds=[SpotId])
            sys.exit(0)

def transfer_eip(region, instanceid, spot_instance):
    eip_address = aws_client(region, resource=False).describe_addresses
    for address in eip_address()["Addresses"]:
        try:
            if address["InstanceId"] == instanceid:
                target_eip = address["AllocationId"]
                print address
        except KeyError:
            pass
    try:
        allocation = aws_client(region, resource=False).allocate_address(Domain='vpc')
        response = aws_client(region, resource=False).associate_address(
            AllocationId=target_eip,
            InstanceId=spot_instance)
    except ClientError as e:
        print e
    
def create_spot_instance(region, instanceid, reserve, instance_name, keep_up, type, keypair, groups, transfer_ip, role, subnet):
    if role:
        LaunchSpecifications["LaunchSpecifications"] = {"Arn": role["Arn"],
                                                        "Name": get_role_name(role["Arn"])}
    if reserve:
        LaunchSpecifications = {
            "SecurityGroupIds": groups,
            "SubnetId": subnet
            }

    client = aws_client(region, resource=False)
    
    spot_offer_price = get_spot_price(region, type)

    LaunchSpecifications = {
        "ImageId": create_ami(region, instanceid, instance_name, no_reboot=keep_up),
        "InstanceType": type,
        "KeyName": keypair,
        "Placement": {"AvailabilityZone": ""}
        }
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
    instance.create_tags(Tags=[{'Key': 'Name', 'Value': 'spot - {}'.format(instance_name)}])
    
    if transfer_ip:
        transfer_eip(instanceid, instance.instance_id)
    
    return instance.public_dns_name


CLICK_CONTEXT_SETTINGS = dict(
    help_option_names=['-h', '--help'],
    token_normalize_func=lambda param: param.lower(),
    ignore_unknown_options=True)

@click.command(context_settings=CLICK_CONTEXT_SETTINGS)
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
@click.option('-v',
              '--verbose',
              is_flag=True,
              help="display run log in verbose mode")
@click.argument('InstanceId')
def spotify(instanceid, dry_run, reserve, keep_up, verbose):
    """Get a Linux distro instance on AWS with one click
    """
    if not keep_up:
        print "Stopping instance"
        stop_instance(instanceid)
    
    instance = get_instance(DEFAULT_REGION, instanceid)
    my_instance = instance_dict(instance)

    if dry_run:
        instance_cost = get_price(get_region_name(DEFAULT_REGION), my_instance["type"], 'Linux')
        spot_instance_cost = get_spot_price(DEFAULT_REGION, my_instance["type"])
        print """Current instance type: {}
On-demand   price: {}
Spot        price: {}""".format(my_instance["type"], instance_cost, spot_instance_cost)
        sys.exit()

    spot_instance = create_spot_instance(DEFAULT_REGION,
                                         instanceid=instanceid, 
                                         reserve=reserve,
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