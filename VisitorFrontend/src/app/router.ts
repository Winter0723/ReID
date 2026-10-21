import { createRouter, createWebHashHistory } from "vue-router";
export const router = createRouter({
  history: createWebHashHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: "/", redirect: "/visits" },
    {
      path: "/sites/:id",
      component: () => import("../features/registration/Welcome.vue"),
    },
    {
      path: "/sites/:id/register",
      component: () => import("../features/registration/Register.vue"),
    },
    {
      path: "/visits",
      component: () => import("../features/visits/Visits.vue"),
    },
    {
      path: "/works",
      component: () => import("../features/visits/Visits.vue"),
      props: { works: true },
    },
    {
      path: "/visits/:id",
      component: () => import("../features/videos/Visit.vue"),
    },
    { path: "/:pathMatch(.*)*", redirect: "/visits" },
  ],
});
