import asyncio,hashlib,os,ssl
import pytest
from cryptography.exceptions import InvalidTag
from enrollment import seal_transfer,open_transfer,create_identity,write_dpapi,read_dpapi

def test_transfer_is_authenticated_and_wrong_password_is_rejected():
    value={'storageKey':'test-only-value','nested':{'data':'hello'}};data=seal_transfer(value,'test-passphrase-123')
    assert b'test-only-value' not in data
    assert open_transfer(data,'test-passphrase-123')==value
    with pytest.raises(InvalidTag):open_transfer(data,'incorrect-passphrase')
    corrupt=bytearray(data);corrupt[-1]^=1
    with pytest.raises(InvalidTag):open_transfer(bytes(corrupt),'test-passphrase-123')

def test_short_passphrase_rejected_and_identity_keys_are_distinct():
    with pytest.raises(ValueError):seal_transfer({},'short')
    value=create_identity()
    assert value['clientFingerprint']!=value['serverFingerprint']
    assert value['clientKeyPem']!=value['serverKeyPem']!=value['caKeyPem']

@pytest.mark.skipif(os.name!='nt',reason='DPAPI is Windows-specific')
def test_windows_identity_write_roundtrips_and_replaces_atomically(tmp_path):
    path=tmp_path/'authority.dpapi';value={'storageKey':'synthetic-test-value'}
    write_dpapi(path,value);assert read_dpapi(path)==value;assert b'synthetic-test-value' not in path.read_bytes()
    write_dpapi(path,{'replacement':True});assert read_dpapi(path)=={'replacement':True}
    assert list(tmp_path.iterdir())==[path]

def test_generated_identity_establishes_tls13_mutual_authentication(tmp_path):
    value=create_identity()
    for field in ['serverCertPem','serverKeyPem','clientCertPem','clientKeyPem']:(tmp_path/field).write_text(value[field])
    async def check():
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.minimum_version=server_context.maximum_version=ssl.TLSVersion.TLSv1_3
        server_context.verify_mode=ssl.CERT_REQUIRED;server_context.load_verify_locations(cadata=value['caPem']);server_context.load_cert_chain(tmp_path/'serverCertPem',tmp_path/'serverKeyPem')
        client_context=ssl.create_default_context(cadata=value['caPem']);client_context.minimum_version=client_context.maximum_version=ssl.TLSVersion.TLSv1_3
        client_context.load_cert_chain(tmp_path/'clientCertPem',tmp_path/'clientKeyPem')
        async def accept(reader,writer):
            assert hashlib.sha256(writer.get_extra_info('ssl_object').getpeercert(binary_form=True)).hexdigest()==value['clientFingerprint']
            await reader.readexactly(1);writer.write(b'y');await writer.drain();writer.close();await writer.wait_closed()
        server=await asyncio.start_server(accept,'127.0.0.1',0,ssl=server_context)
        try:
            reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1],ssl=client_context,server_hostname='chatbridge-gateway')
            assert writer.get_extra_info('ssl_object').version()=='TLSv1.3';writer.write(b'x');await writer.drain();assert await reader.readexactly(1)==b'y';writer.close();await writer.wait_closed()
        finally:server.close();await server.wait_closed()
    asyncio.run(check())
