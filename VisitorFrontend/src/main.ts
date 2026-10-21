import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./app/App.vue";
import { router } from "./app/router";
import "./shared/style.css";
import "vant/es/dialog/style";
import "vant/es/toast/style";
createApp(App).use(createPinia()).use(router).mount("#app");
