"""Follow the approved multipart recovery protocol without weakening field checks."""


def follow_upload_recovery(client, response):
    assert response.status_code == 302
    assert "upload_draft=" in response.url
    recovered = client.get(response.url)
    assert recovered.status_code == 200
    return recovered
