from backend.bl_haos.multiroom.manager import MultiroomManager


def test_multiroom_speaker_attachment_and_grouping():
    manager = MultiroomManager()

    # Attach speaker 1
    spk1_addr = "11:22:33:44:55:66"
    client1 = manager.attach_speaker(spk1_addr, "Living Room Speaker", latency_offset_ms=-15)
    assert client1.speaker_address == spk1_addr.lower()
    assert client1.latency_offset_ms == -15

    # Attach speaker 2
    spk2_addr = "AA:BB:CC:DD:EE:FF"
    client2 = manager.attach_speaker(spk2_addr, "Kitchen Speaker", latency_offset_ms=10)
    assert client2.speaker_address == spk2_addr.lower()
    assert client2.latency_offset_ms == 10

    # Verify default group contains both clients
    groups = manager.get_groups()
    assert len(groups) == 1
    assert client1.client_id in groups[0].client_ids
    assert client2.client_id in groups[0].client_ids

    # Test setting latency offset
    updated = manager.set_latency_offset(spk1_addr, -25)
    assert updated is True
    assert manager.clients[client1.client_id].latency_offset_ms == -25

    # Test creating custom group
    custom_group = manager.create_group("party_zone", "Party Zone", [client1.client_id])
    assert custom_group.group_id == "party_zone"
    assert len(manager.get_groups()) == 2

    # Test detaching speaker
    detached = manager.detach_speaker(spk2_addr)
    assert detached is True
    assert client2.client_id not in manager.clients
    assert client2.client_id not in manager.groups["default"].client_ids
