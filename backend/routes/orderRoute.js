import express from 'express';
import authMiddleware from '../middleware/auth.js';
import { placeOrder, verifyOrder, userOrders, listOrders, updateStatus, handleStripeWebhook, reconcileOrder} from '../controllers/orderController.js';

const orderRouter = express.Router();

orderRouter.post('/place', authMiddleware, placeOrder);
orderRouter.post('/verify', verifyOrder);
orderRouter.post('/userorders', authMiddleware, userOrders);
orderRouter.get('/list', listOrders);
orderRouter.post('/status', updateStatus);
orderRouter.post('/webhook', express.raw({ type: 'application/json' }), handleStripeWebhook);
orderRouter.post('/reconcile/:orderId', authMiddleware, reconcileOrder);

export default orderRouter;