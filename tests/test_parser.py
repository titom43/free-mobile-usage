from free_mobile_usage.client import parse_usage_html


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
