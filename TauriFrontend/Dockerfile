FROM node:22-alpine AS build
WORKDIR /workspace/TauriFrontend
COPY TauriFrontend/package*.json ./
RUN npm ci
COPY TauriFrontend/ ./
# Browser clients use the same-origin nginx proxy; never bake a private API URL in.
ENV VITE_MYREID_API_BASE_URL=""
RUN npm run build

FROM nginx:alpine
COPY TauriFrontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/TauriFrontend/dist /usr/share/nginx/html
EXPOSE 80
