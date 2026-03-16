def test_molecule_run(host):
    f = host.file('/tmp/migrate_server.py')
    assert f.exists
    assert f.mode & 0o100
