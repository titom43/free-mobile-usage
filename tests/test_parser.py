from free_mobile_usage.client import parse_usage_html
from free_mobile_usage.models import FreeMobileUsageData


def test_parse_basic_usage_values():
    html = """
    <html><body>
      <div>Titulaire : Jean Test</div>
      <div>06 12 34 56 78</div>
      <div>Internet 12,5 Go</div>
      <div>Hors forfait 1,23 €</div>
      <div>Roaming 256 Mo</div>
    </body></html>
    """

    data = parse_usage_html(html, fallback_data_limit_gb=250)

    assert data.phone_number == "06 12 34 56 78"
    assert data.data_used_gb == 12.5
    assert data.data_limit_gb == 250
    assert data.data_remaining_gb == 237.5
    assert data.data_used_percent == 5.0
    assert data.out_of_plan_eur == 1.23
    assert data.roaming_data_used_gb == 0.25


def test_parse_mobile_api_call_and_message_counters():
    data = FreeMobileUsageData.from_mobile_api(
        line_id="line",
        account_name="Test",
        phone_number=None,
        plan_type="test",
        consumption={
            "national": {
                "consumption": {
                    "voice": {"nationalVoiceTime": 1134, "internationalVoiceTime": 42},
                    "sms": 3,
                    "mms": 1,
                    "data": 0,
                },
                "billing": {"data": 0},
            },
            "roaming": {
                "consumption": {
                    "voice": {"roamingOutgoingVoiceTime": 120, "roamingIncomingVoiceTime": 372},
                    "sms": 2,
                    "mms": 0,
                    "data": 0,
                },
                "billing": {"data": 0},
            },
        },
        offer={},
    )

    assert data.national_voice_seconds == 1134
    assert data.national_international_voice_seconds == 42
    assert data.roaming_outgoing_voice_seconds == 120
    assert data.roaming_incoming_voice_seconds == 372
    assert data.national_sms == 3
    assert data.national_mms == 1
    assert data.roaming_sms == 2
    assert data.roaming_mms == 0
