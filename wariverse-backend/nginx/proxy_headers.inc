# Shared proxy headers.
#
# X-Forwarded-Proto is not cosmetic here: uvicorn runs with --proxy-headers, and
# the Twilio IVR signature check hashes the public URL. Without the real scheme
# the app would build an http:// URL, the HMAC would not match what Twilio
# signed, and every voice webhook would 403.
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header Connection        "";
