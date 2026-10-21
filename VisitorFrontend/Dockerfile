FROM node:22-alpine AS build
WORKDIR /app
COPY VisitorFrontend/package*.json ./
RUN npm ci
COPY VisitorFrontend/ ./
RUN npm run build
FROM nginx:alpine
COPY VisitorFrontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html/visitor
EXPOSE 80
