import express from 'express';
import cors from 'cors';
import { connectDB } from './config/db.js';
import connectCloudinary from './config/cloudinary.js';
import foodRouter from './routes/foodRoute.js';
import userRouter from './routes/userRoute.js';
import cartRouter from './routes/cartRoute.js';
import orderRouter from './routes/orderRoute.js';
import chatRouter from './routes/chatRoute.js';
import { handleStripeWebhook } from './controllers/orderController.js'; 
import 'dotenv/config';

const app = express();
const port = process.env.PORT || 4000;

// ---------- Core middleware (CORS first) ----------
app.use(cors());

// ⚠️ Stripe webhook must receive RAW body and be mounted BEFORE express.json():
app.post('/api/order/webhook', express.raw({ type: 'application/json' }), handleStripeWebhook);

// JSON parser for the rest of the app
app.use(express.json({ limit: '10kb' }));

// ---------- DB / Cloud ----------
connectDB();
connectCloudinary();

// ---------- Routes ----------
app.use('/api/food', foodRouter);
app.use('/api/user', userRouter);
app.use('/api/cart', cartRouter);
app.use('/api/order', orderRouter); // includes /place, /verify, /list, /status, etc.
app.use('/api/chat', chatRouter);

// ---------- Health ----------
app.get('/', (req, res) => {
  res.send('API working');
});

app.listen(port, () => {
  console.log(`Server started on http://localhost:${port}`);
});