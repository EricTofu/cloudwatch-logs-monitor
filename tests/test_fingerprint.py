from log_monitor.fingerprint import generate_fingerprint, mask_message


def test_mask_message():
    assert mask_message(None) == ""
    assert mask_message("") == ""

    # UUID masking
    msg1 = "User 123e4567-e89b-12d3-a456-426614174000 failed to login"
    assert mask_message(msg1) == "User <UUID> failed to login"

    # IP masking
    msg2 = "Connection timeout from 192.168.1.100"
    assert mask_message(msg2) == "Connection timeout from <IP>"

    # Timestamp masking
    msg3 = "Exception at 2026-03-04 12:00:00: NullPointerException"
    assert mask_message(msg3) == "Exception at <TIMESTAMP>: NullPointerException"

    # Hex masking
    msg4 = "Memory leak at address 0xdeadbeef"
    assert mask_message(msg4) == "Memory leak at address <HEX>"

    # Long number masking
    msg5 = "Transaction 1234567 took 500ms"
    assert mask_message(msg5) == "Transaction <NUM> took 500ms"

    # Complex masking (all combined)
    # Note: simple thread IDs or partial UUIDs might not match the regex, but the goal is structural similarity.


def test_generate_fingerprint_consistency():
    msg_a1 = "NullPointerException in user service for user id 12345"
    msg_a2 = "NullPointerException in user service for user id 99999"

    fp_a1 = generate_fingerprint(msg_a1)
    fp_a2 = generate_fingerprint(msg_a2)
    assert fp_a1 == fp_a2, "Messages with different IDs should have the same fingerprint"

    msg_b = "ConnectionTimeout in database service"
    fp_b = generate_fingerprint(msg_b)
    assert fp_a1 != fp_b, "Different error structures should have different fingerprints"
