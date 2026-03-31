tunnel: __TUNNEL_ID__
credentials-file: /etc/cloudflared/__TUNNEL_ID__.json


ingress:
  - hostname: __PUBLIC_HOSTNAME__
    service: http://127.0.0.1:__SERVER_PORT__
  - service: http_status:404
