"""
Use environment variable: OWR_USE_TEST_SOURCES=1
"""
import json
import sys
import random
import time
from gi.repository import GLib
from gi.repository import Gio
from gi.repository import Owr
from gi.repository import Soup

SERVER_URL = "http://demo.openwebrtc.org:38080"

ALL_SESSIONS = []
LOCAL_SOURCES = []
TRANSPORT_AGENT = None


def got_remote_source(session, source):
    print("Got remote source")


def got_candidate(session, candidate):
    print("Got candidate")


def candidate_gathering_done(session):
    print("Candidate gathering done")


def got_dtls_certificate(session, pspec):
    print("Got DTLS Certificate")


def reset():
    global LOCAL_SOURCES, TRANSPORT_AGENT, ALL_SESSIONS
    print("Reset")
    for session, session_data in ALL_SESSIONS:
        session.set_send_source(None)
    ALL_SESSIONS = []
    TRANSPORT_AGENT = None
    LOCAL_SOURCES = []
    Owr.get_capture_sources(Owr.MediaType.VIDEO, got_local_sources)


def candidate_from_description(candidate_description):
    candidate_type = candidate_description['type']
    if candidate_type == 'host':
        candidate_type = Owr.CandidateType.HOST
    elif candidate_type == 'srflx':
        candidate_type = Owr.CandidateType.SERVER_REFLEXIVE
    else:
        candidate_type = Owr.CandidateType.RELAY
    component_type = int(candidate_description['componentId'])
    remote_candidate = Owr.Candidate.new(candidate_type, component_type)
    foundation = candidate_description['foundation']
    remote_candidate.props.foundation = foundation
    transport = candidate_description['transport']
    if transport == 'UDP':
        transport = Owr.TransportType.UDP
    else:
        transport = Owr.TransportType.TCP_ACTIVE

    if transport != Owr.TransportType.UDP:
        tcp_type = candidate_description['tcpType']
        if tcp_type == 'active':
            transport = Owr.TransportType.TCP_ACTIVE
        elif tcp_type == 'passive':
            transport = Owr.TransportType.TCP_PASSIVE
        else:
            transport = Owr.TransportType.TCP_SO
    remote_candidate.props.transport_type = transport
    remote_candidate.props.address = candidate_description["address"]
    remote_candidate.props.port = int(candidate_description["port"])
    remote_candidate.props.priority = int(candidate_description["priority"])
    return remote_candidate


def handle_offer(message):
    print("Handling sdp offer")
    data = json.loads(message)
    media_descriptions = data["sessionDescription"]["mediaDescriptions"]
    for description in media_descriptions:
        session = Owr.MediaSession.new(True)
        media_type = description["type"]
        session_data = {}
        session_data['media-type'] = media_type
        session.props.rtcp_mux = bool(description["rtcp"]["mux"])
        payloads = description["payloads"]
        codec_type = Owr.CodecType.NONE
        for payload in payloads:
            encoding_name = payload["encodingName"]
            payload_type = int(payload["type"])
            clock_rate = int(payload["clockRate"])
            send_payload = None
            receive_payload = None
            if media_type == 'audio':
                media_type = Owr.MediaType.AUDIO
                if encoding_name == 'PCMA':
                    codec_type = Owr.CodecType.PCMA
                elif encoding_name == 'PCMU':
                    codec_type = Owr.CodecType.PCMU
                elif encoding_name == 'OPUS':
                    codec_type = Owr.CodecType.OPUS
                else:
                    continue
                channels = int(payload["channels"])
                send_payload = Owr.AudioPayload.new(codec_type, payload_type, clock_rate, channels)
                receive_payload = Owr.AudioPayload.new(codec_type, payload_type, clock_rate, channels)
            elif media_type == 'video':
                media_type = Owr.MediaType.VIDEO
                if encoding_name == 'H264':
                    codec_type = Owr.CodecType.H264
                elif encoding_name == 'VP8':
                    codec_type = Owr.CodecType.VP8
                else:
                    continue
                ccm_fir = bool(payload["ccmfir"])
                nack_pli = bool(payload["nackpli"])
                send_payload = Owr.VideoPayload.new(codec_type, payload_type, clock_rate, ccm_fir, nack_pli)
                receive_payload = Owr.VideoPayload.new(codec_type, payload_type, clock_rate, ccm_fir, nack_pli)
            else:
                print("Media type: %s not supported" % (media_type,))
                continue

            if send_payload and receive_payload:
                session_data['encoding-name'] = encoding_name
                session_data['payload-type'] = payload_type
                session_data['clock-rate'] = clock_rate
                if media_type == Owr.MediaType.AUDIO:
                    session_data['channels'] = channels
                elif media_type == Owr.MediaType.VIDEO:
                    session_data['ccm-fir'] = ccm_fir
                    session_data['nack-pli'] = nack_pli
                session.add_receive_payload(receive_payload)
                session.set_send_payload(send_payload)
                break

        ice_ufrag = description["ice"]["ufrag"]
        session_data['remote-ice-ufrag'] = ice_ufrag
        ice_password = description["ice"]["password"]
        session_data['remote-ice-password'] = ice_password
        for candidate in description["ice"].get("candidates", []):
            remote_candidate = candidate_from_description(candidate)
            remote_candidate.props.ufrag = ice_ufrag
            component_type = remote_candidate.props.component_type
            if not rtcp_mux or component_type != Owr.ComponentType.RTCP:
                session.add_remote_candidate(remote_candidate)
        session.connect("on-incoming-source", got_remote_source)
        session.connect("on-new-candidate", got_candidate)
        session.connect("on-candidate-gathering-done", candidate_gathering_done)
        session.connect("notify::dtls-certificate", got_dtls_certificate)

        for source in LOCAL_SOURCES:
            if media_type == source.props.media_type:
                session.set_send_source(source)
        ALL_SESSIONS.append((session, session_data))
        TRANSPORT_AGENT.add_session(session)


def handle_remote_candidate(message):
    print("Handling remote candidate")
    data = json.loads(message)
    sdp_mline_index = data["candidate"]["sdpMLineIndex"]
    candidate_description = data["candidate"]["candidateDescription"]
    remote_candidate = candidate_from_description(candidate_description)
    # stuff related to the media session
    print("Remote candidate parsed: %s" % (remote_candidate,))


def eventstream_line_read(input_stream, result, peer_joined):
    line = input_stream.read_line_finish_utf8(result)
    print("Got line of length: %d (%s)" % (line[1], line[0]))
    if line[0]:
        if peer_joined and line[0].startswith('data:'):
            peer_joined = False
            peer_id = line[0][5:]
            print("Peer joined: " + peer_id)
        elif line[0].startswith('event:leave'):
            print("Peer left")
            peer_id = ''
            reset()
        elif line[0].startswith('event:join'):
            peer_joined = True
        elif line[0][7:].startswith('sdp'):
            handle_offer(line[0][5:])
        elif line[0][7:].startswith('candidate'):
            handle_remote_candidate(line[0][5:])
    read_eventstream_line(input_stream, peer_joined)


def read_eventstream_line(input_stream, peer_joined=False):
    input_stream.read_line_async(GLib.PRIORITY_DEFAULT, None, eventstream_line_read, peer_joined)


def eventsource_request_sent(session, result, _data):
    print("request sent")
    input_stream = session.send_finish(result)
    if input_stream:
        data_input_stream = Gio.DataInputStream.new(input_stream)
        read_eventstream_line(data_input_stream)
    else:
        print("error")
        Owr.quit()


def send_eventsource_request(url):
    session = Soup.Session.new()
    message = Soup.Message.new("GET", url)
    print("got here: 1 " + url)
    session.send_async(message, None, eventsource_request_sent, None)
    print("got here: 2")


def got_local_sources(sources):
    global LOCAL_SOURCES
    global TRANSPORT_AGENT
    LOCAL_SOURCES = sources
    print(sources)
    ta = Owr.TransportAgent.new(False)
    ta.add_helper_server(Owr.HelperServerType.STUN, "stun.services.mozilla.com", 3478, None, None)
    TRANSPORT_AGENT = ta
    url = SERVER_URL + '/stoc/%s/%d' % (sys.argv[1], random.randint(0, pow(2, 32)-1))
    send_eventsource_request(url)
    print("got here: 3")


def main():
    mc = GLib.MainContext.get_thread_default()
    if not mc:
        mc = GLib.MainContext.default()
    Owr.init(mc)
    Owr.get_capture_sources(Owr.MediaType.VIDEO, got_local_sources)
    Owr.run()
    print("exiting")

if __name__ == '__main__':
    main()