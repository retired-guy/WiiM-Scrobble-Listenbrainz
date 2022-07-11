#!/usr/bin/python3
# -*- coding: utf-8 -*-
# pylint: disable=invalid-name

import pylistenbrainz
from time import sleep
import requests
import re
import configparser

import asyncio
import json
import logging
import operator
import sys
import time
import xmltodict
from datetime import datetime
from typing import Any, Optional, Sequence, Tuple, Union, cast
from collections import OrderedDict

from async_upnp_client.advertisement import SsdpAdvertisementListener
from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpRequester
from async_upnp_client.client import UpnpDevice, UpnpService, UpnpStateVariable
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.const import NS, AddressTupleVXType, SsdpHeaders
from async_upnp_client.exceptions import UpnpResponseError
from async_upnp_client.profiles.dlna import dlna_handle_notify_last_change
from async_upnp_client.search import async_search as async_ssdp_search
from async_upnp_client.ssdp import SSDP_IP_V4, SSDP_IP_V6, SSDP_PORT, SSDP_ST_ALL
from async_upnp_client.utils import get_local_ip

##################################################
### READ CONFIG FILE #############################
config = configparser.ConfigParser()
config.read('scrobble.config')
auth_token=config['listenbrainz']['token']
description_url=config['wiim']['description_url']
##################################################

client = pylistenbrainz.ListenBrainz()
client.set_auth_token(auth_token)
listen = pylistenbrainz.Listen(
            track_name='',
            artist_name='',
            release_name='',
            listened_at=int(time.time()),
         )

items = {} 

event_handler = None
playing = False
status = ""

async def create_device(description_url: str) -> UpnpDevice:
    """Create UpnpDevice."""
    timeout = 60
    non_strict = True
    requester = AiohttpRequester(timeout)
    factory = UpnpFactory(requester, non_strict=non_strict)
    return await factory.async_create_device(description_url)


def get_timestamp() -> Union[str, float]:
    """Timestamp depending on configuration."""
    return time.time()


def service_from_device(device: UpnpDevice, service_name: str) -> Optional[UpnpService]:
    """Get UpnpService from UpnpDevice by name or part or abbreviation."""
    for service in device.all_services:
        part = service.service_id.split(":")[-1]
        abbr = "".join([c for c in part if c.isupper()])
        if service_name in (service.service_type, part, abbr):
            return service

    return None

def on_event(
    service: UpnpService, service_variables: Sequence[UpnpStateVariable]
) -> None:
    """Handle a UPnP event."""
    obj = {
        "timestamp": get_timestamp(),
        "service_id": service.service_id,
        "service_type": service.service_type,
        "state_variables": {sv.name: sv.value for sv in service_variables},
    }
    global playing
    global items
    global listen
    global status
    
    # special handling for DLNA LastChange state variable
    if len(service_variables) == 1 and service_variables[0].name == "LastChange":
        last_change = service_variables[0]
        dlna_handle_notify_last_change(last_change)
    else:
        for sv in service_variables:
            ### PAUSED, PLAYING, STOPPED, etc
            #print(sv.name,sv.value)
            if sv.name == "TransportState":
                #print(sv.value)
                status = sv.value
                if sv.value == "PLAYING":
                  playing = True
                else:
                  playing = False

            ### Grab the metadata
            if sv.name == "CurrentTrackMetaData": ### or sv.name == "AVTransportURIMetaData":
                ### Convert the grubby XML to beautiful JSON, because we HATE XML!
                items = xmltodict.parse(sv.value)["DIDL-Lite"]["item"]
                ### Print the entire mess
                #print(json.dumps(items,indent=4))

                ###  Set up the listen data
                try:
                  title = items["dc:title"]
                  i = title.find('(')
                  if i > 5:
                    title=title[0:i-1]

                  #print("Title:",title)
                  listen.track_name=title
                except:
                  pass

                try:
                  artist = items["upnp:artist"]
                  #print("Artist:",artist)
                  listen.artist_name=artist
                except:
                  pass

                try:
                  album = items["upnp:album"]
                  i = album.find('(')
                  if i > 5:
                    album = album[0:i-1]

                  #print("Album:",album)
                  listen.release_name=album
                except:
                  pass


                if status=="TRANSITIONING":
                  try:
                    listen = pylistenbrainz.Listen(
                      track_name=title,
                      artist_name=artist,
                      release_name=album,
                      listened_at=int(time.time()),
                    )
                    response = client.submit_single_listen(listen)

                  except Exception as e:
                    print(e)
                    pass


async def subscribe(description_url: str, service_names: Any) -> None:
    """Subscribe to service(s) and output updates."""
    global event_handler  # pylint: disable=global-statement

    device = await create_device(description_url)

    # start notify server/event handler
    source = (get_local_ip(device.device_url), 0)
    server = AiohttpNotifyServer(device.requester, source=source)
    await server.async_start_server()

    # gather all wanted services
    if "*" in service_names:
        service_names = device.services.keys()

    services = []

    for service_name in service_names:
        service = service_from_device(device, service_name)
        if not service:
            print(f"Unknown service: {service_name}")
            sys.exit(1)
        service.on_event = on_event
        services.append(service)

    # subscribe to services
    event_handler = server.event_handler
    for service in services:
       try:
            await event_handler.async_subscribe(service)
       except UpnpResponseError as ex:
            print("Unable to subscribe to %s: %s", service, ex)

    s = 0
    # keep the webservice running
    while True:
        await asyncio.sleep(10)
        s = s + 1
        if s >= 12:
          await event_handler.async_resubscribe_all()
          s = 0

async def async_main() -> None:
    """Async main."""

    service = ["AVTransport"]
    await subscribe(description_url, service)


def main() -> None:
    """Set up async loop and run the main program."""
    loop = asyncio.get_event_loop()

    try:
        loop.run_until_complete(async_main())
    except KeyboardInterrupt:
        if event_handler:
            loop.run_until_complete(event_handler.async_unsubscribe_all())
    finally:
        loop.close()


if __name__ == "__main__":
    main()


