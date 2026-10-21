<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { CheckCircle2, CircleDollarSign } from "@lucide/vue";
import { apiRequest, downloadAsset, normalizeAssetUrl } from "../lib/api/http";
import type { ScenicOrder } from "../lib/api/scenic";
import { useWorkbenchStore } from "../stores/workbench";

const store = useWorkbenchStore();
const amountCents = ref(0);
const selectedVlogId = ref(0);
const copiedOrder = ref(0);
const readyVlogs = computed(() => (store.selectedTourist?.vlog_jobs || []).filter((vlog) => vlog.status === "succeeded" && (!vlog.variants?.length || (vlog.variants.length === 2 && vlog.variants.every((variant) => variant.status === "succeeded")))));
watch(() => store.selectedTourist?.id, () => { selectedVlogId.value = 0; });
watch(readyVlogs, (items) => { if (!items.some((item) => item.id === selectedVlogId.value)) selectedVlogId.value = 0; });
const canSimulatePayments = import.meta.env.DEV;
const orders = computed(() => store.selectedTourist?.orders || []);
const canConfirmVisitorPayment = ref(false);
let permissionGeneration = 0;
watch(() => [store.scenicProjectId, store.scenicUser?.username, orders.value.some(order => order.payment_provider === "visitor_manual")], async ([projectId, , hasManual]) => {
  const current = ++permissionGeneration;
  canConfirmVisitorPayment.value = false;
  if (!projectId || !hasManual) return;
  try {
    const result = await apiRequest<{ can_confirm_payment: boolean }>(`/api/v1/scenic/projects/${projectId}/visitor-queue`, { authScope: "scenic" });
    if (current === permissionGeneration) canConfirmVisitorPayment.value = result.can_confirm_payment;
  } catch { /* The server still enforces project management permission. */ }
}, { immediate: true });
let paymentPollTimer: number | null = null;
let paymentPollRunning = false;

function isWechatOrder(order: ScenicOrder): boolean {
  return order.payment_provider === "wechat_native" || order.payment_method === "wechat_native";
}

function isPendingWechatOrder(order: ScenicOrder): boolean {
  return isWechatOrder(order) && order.status !== "paid" && order.status !== "payment_failed";
}

function clearPaymentPollTimer(): void {
  if (paymentPollTimer !== null) {
    window.clearInterval(paymentPollTimer);
    paymentPollTimer = null;
  }
}

async function pollWechatPaymentOrders(): Promise<void> {
  if (paymentPollRunning) return;
  const order = orders.value.find(isPendingWechatOrder);
  if (!order) {
    clearPaymentPollTimer();
    return;
  }
  paymentPollRunning = true;
  try {
    await store.refreshScenicOrderPayment(order.id, true);
  } finally {
    paymentPollRunning = false;
  }
}

function syncPaymentPolling(): void {
  if (!orders.value.some(isPendingWechatOrder)) {
    clearPaymentPollTimer();
    return;
  }
  if (paymentPollTimer === null) {
    paymentPollTimer = window.setInterval(() => void pollWechatPaymentOrders(), 5000);
  }
}

async function resolveLocalPayment(orderId: number): Promise<void> {
  const confirmed = window.confirm("模拟支付：点击“确定”代表支付成功，点击“取消”代表支付失败。");
  const order = await store.completeSimulatedPayment(orderId, confirmed ? "success" : "failed");
  if (!confirmed && order) window.alert(order.payment_error || "支付失败，请重试并重新付款。");
}

async function startWechatPayment(): Promise<void> {
  const order = await store.createWechatPayment(amountCents.value, selectedVlogId.value);
  if (canSimulatePayments && order?.id) await resolveLocalPayment(order.id);
}

async function copyDelivery(order: ScenicOrder): Promise<void> {
  const source = order.delivery_page_url;
  if (!source) return;
  try { await navigator.clipboard.writeText(new URL(normalizeAssetUrl(source), window.location.origin).href); copiedOrder.value = order.id; }
  catch { store.error = "复制失败，请打开领取页面后复制地址。"; }
}

function money(cents?: number): string {
  return `¥${((cents || 0) / 100).toFixed(2)}`;
}

function orderDownloadSource(order: ScenicOrder): string {
  return normalizeAssetUrl(order.full_url || order.preview_url || "");
}

function orderQrSource(order: ScenicOrder): string {
  return normalizeAssetUrl(order.payment_qr_url || "");
}

async function downloadOrder(order: ScenicOrder): Promise<void> {
  const source = order.full_url || order.preview_url || "";
  if (!source) return;
  try {
    await downloadAsset(source, `scenic_order_${order.id}.mp4`, "scenic");
  } catch (error) {
    store.error = error instanceof Error ? error.message : "下载失败";
  }
}

function paymentStateLabel(order: ScenicOrder): string {
  if (order.status === "paid") return "已支付";
  if (order.status === "payment_failed") return "支付失败";
  if (order.payment_provider === "visitor_manual") return "等待景区确认付款";
  const labels: Record<string, string> = {
    NOTPAY: "待扫码/待支付",
    USERPAYING: "用户支付中",
    ACCEPT: "已受理",
    SUCCESS: "已支付",
    CLOSED: "已关闭",
    PAYERROR: "支付异常",
    REFUND: "已退款",
  };
  return labels[order.provider_trade_state || ""] || (isWechatOrder(order) ? "等待支付" : order.status);
}

function canManualConfirm(order: ScenicOrder): boolean {
  if (order.payment_provider === "visitor_manual") return canConfirmVisitorPayment.value && order.status === "pending" && order.provider_trade_state === "WAITING_STAFF";
  return store.scenicUser?.role === "admin" && order.status !== "paid";
}
async function confirmPayment(order: ScenicOrder): Promise<void> {
  if (!canManualConfirm(order)) return;
  if (order.payment_provider === "visitor_manual" && !window.confirm(`确认游客付款：${order.order_no} · ${money(order.amount_cents)}。核对后开放完整视频下载？`)) return;
  await store.markScenicOrderPaid(order.id);
}

watch(
  () => orders.value.map((order) => `${order.id}:${order.status}:${order.provider_trade_state || ""}`).join("|"),
  syncPaymentPolling,
  { immediate: true },
);

onUnmounted(() => { permissionGeneration++; clearPaymentPollTimer(); });
</script>

<template>
  <article class="payment-panel">
    <div class="payment-head">
      <h3>订单与交付</h3>
      <span>{{ orders.length }} 单</span>
    </div>

    <div class="payment-controls">
      <label><span>交付成片版本</span><select v-model.number="selectedVlogId" aria-label="交付成片版本"><option :value="0">请选择已完成版本</option><option v-for="vlog in readyVlogs" :key="vlog.id" :value="vlog.id">版本 #{{ vlog.id }} · {{ vlog.variants?.length ? '横竖双版' : '单视频' }}</option></select></label>
      <label>
        <span>订单金额（分，以景区定价为准）</span>
        <input v-model.number="amountCents" min="1" step="100" type="number" />
      </label>
      <button type="button" :disabled="!selectedVlogId || amountCents < 1" @click="store.createScenicOrder(amountCents, selectedVlogId)">
        <CircleDollarSign :size="15" />
        <span>创建订单</span>
      </button>
      <button class="primary" type="button" :disabled="!selectedVlogId || amountCents < 1" @click="startWechatPayment">
        <CheckCircle2 :size="15" />
        <span>{{ canSimulatePayments ? "模拟支付" : "创建微信支付" }}</span>
      </button>
    </div>

    <div v-if="orders.length" class="order-list">
      <div v-for="order in orders" :key="order.id" class="order-row">
        <img v-if="order.status === 'paid' && order.delivery_qr_url" class="payment-qr" :src="normalizeAssetUrl(order.delivery_qr_url)" alt="游客领取二维码" />
        <img v-else-if="orderQrSource(order)" class="payment-qr" :src="orderQrSource(order)" alt="支付二维码" />
        <div class="order-copy">
          <strong>{{ order.order_no || `订单 #${order.id}` }} · {{ money(order.amount_cents) }}</strong>
          <span>{{ paymentStateLabel(order) }}</span>
          <small v-if="order.vlog_job_id">交付版本 #{{ order.vlog_job_id }} · {{ order.variants?.length ? '横竖双版' : '纪念视频' }}</small>
          <small v-if="order.status === 'paid'">{{ order.claimed_at ? `游客已确认领取 · ${new Date(order.claimed_at).toLocaleString()}` : '已开放领取 · 等待游客确认' }}</small>
          <small v-if="order.payment_error" class="payment-error">{{ order.payment_error }}</small>
        </div>
        <div class="order-actions">
          <a v-if="order.delivery_page_url" :href="normalizeAssetUrl(order.delivery_page_url)" target="_blank" rel="noopener">打开领取页</a>
          <button v-if="order.delivery_page_url" type="button" @click="copyDelivery(order)">{{ copiedOrder === order.id ? '链接已复制' : '复制领取链接' }}</button>
          <button v-if="canSimulatePayments && isWechatOrder(order)" type="button" :disabled="order.status === 'paid'" @click="resolveLocalPayment(order.id)">
            {{ order.status === "payment_failed" ? "重新付款" : "模拟付款" }}
          </button>
          <button v-if="isWechatOrder(order)" type="button" :disabled="order.status === 'paid'" @click="store.refreshScenicOrderPayment(order.id)">刷新状态</button>
          <button v-if="canManualConfirm(order)" type="button" @click="confirmPayment(order)">{{ order.payment_provider === 'visitor_manual' ? '确认游客付款' : '管理员确认' }}</button>
          <button type="button" :disabled="!orderDownloadSource(order)" @click="downloadOrder(order)">下载</button>
        </div>
      </div>
    </div>
    <div v-else class="payment-empty">{{ canSimulatePayments ? "当前为测试环境，模拟支付不会产生真实扣款。" : "创建微信支付订单后可扫码付款并刷新状态。" }}</div>
  </article>
</template>

<style scoped>
.payment-panel {
  padding: 18px;
  border: 1px solid #d9e1ea;
  border-radius: 16px;
  background: #fff;
  color: #0f172a;
}

.payment-head,
.payment-controls,
.order-row,
.order-actions {
  display: flex;
  align-items: center;
}

.payment-head {
  justify-content: space-between;
  margin-bottom: 14px;
}

.payment-head h3 { margin: 0; font-size: 15px; }
.payment-head span { color: #64748b; font-size: 12px; }

.payment-controls {
  flex-wrap: wrap;
  gap: 9px;
  margin-bottom: 14px;
}

.payment-controls label {
  display: grid;
  gap: 4px;
}

.payment-controls label span { color: #64748b; font-size: 10px; }
.payment-controls input,.payment-controls select {
  width: 190px;
  padding: 9px 11px;
  border: 1px solid #ccd6e2;
  border-radius: 9px;
  background: #f8fafc;
  color: #0f172a;
}

button,
.order-actions a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 36px;
  padding: 8px 11px;
  border: 1px solid #cbd5e1;
  border-radius: 9px;
  background: #f8fafc;
  color: #1e293b;
  font-size: 12px;
  font-weight: 650;
  text-decoration: none;
  cursor: pointer;
}

button.primary { border-color: #2563eb; background: #2563eb; color: #fff; }
button:disabled,
.order-actions a.disabled { pointer-events: none; opacity: 0.45; }

.order-list { display: grid; gap: 9px; }
.order-row {
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
}

.payment-qr { width: 72px; height: 72px; border-radius: 8px; object-fit: contain; }
.order-copy { display: grid; min-width: 0; flex: 1; gap: 3px; }
.order-copy strong { font-size: 12px; }
.order-copy span { color: #2563eb; font-size: 12px; font-weight: 700; }
.order-copy small { overflow-wrap: anywhere; color: #64748b; }
.order-copy .payment-error { color: #dc2626; }
.order-actions { flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.order-actions button,
.order-actions a { min-height: 30px; padding: 6px 9px; font-size: 11px; }
.payment-empty { padding: 18px; border: 1px dashed #cbd5e1; border-radius: 12px; color: #64748b; font-size: 12px; text-align: center; }

@media (max-width: 800px) {
  .order-row { flex-wrap: wrap; }
  .order-actions { width: 100%; justify-content: flex-start; }
}
</style>
