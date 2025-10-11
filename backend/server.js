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

// --- CORS: sanitize origins and include custom headers ---
const ALLOWED_ORIGINS = [
  'http://localhost:5173',
  process.env.FRONTEND_URL,
].filter(Boolean); // drop undefined/empty

app.use(
  cors({
    origin: (origin, cb) => {
      // allow no-origin (mobile apps/postman) or listed origins
      if (!origin || ALLOWED_ORIGINS.includes(origin)) return cb(null, true);
      return cb(new Error(`CORS: origin ${origin} not allowed`));
    },
    credentials: true,
    methods: ['GET', 'POST', 'OPTIONS'],
    allowedHeaders: [
      'Content-Type',
      'Authorization',
      'X-User-JWT',
      'X-User-Cookie',
      'x-service-auth',
    ],
  })
);
// Optional explicit preflight handler (helps some hosts)
app.options('*', cors());

// --- Stripe webhook: RAW body BEFORE express.json ---
app.post(
  '/api/order/webhook',
  express.raw({ type: 'application/json' }),
  handleStripeWebhook
);

// Normal JSON parsing for the rest
app.use(express.json({ limit: '10kb' }));

// --- Infra ---
connectDB();
connectCloudinary();

// --- Routes ---
app.use('/api/food', foodRouter);
app.use('/api/user', userRouter);
app.use('/api/cart', cartRouter);
app.use('/api/order', orderRouter);
app.use('/api/chat', chatRouter); // this must forward to Python

// --- Health ---
app.get('/', (_req, res) => res.send('API working'));

app.listen(port, () => {
  console.log(`Server started on http://localhost:${port}`);
});