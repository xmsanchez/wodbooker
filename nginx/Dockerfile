FROM nginx:latest

RUN apt update
RUN apt install -y certbot python3-certbot-nginx

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d
