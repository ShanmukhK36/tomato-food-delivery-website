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

/* ----------------------- CORS (global) ----------------------- */
/* Allow your frontend and custom headers used by the chat proxy */
const ALLOWED_ORIGINS = [
  'http://localhost:5173',
  process.env.FRONTEND_URL,
  process.env.ADMIN_URL,
].filter(Boolean);

app.use(
  cors({
    origin: (origin, cb) => {
      // allow requests without an Origin (curl, server-to-server)
      if (!origin) return cb(null, true);
      if (ALLOWED_ORIGINS.includes(origin)) return cb(null, true);
      return cb(new Error(`CORS: origin ${origin} not allowed`));
    },
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: [
      'Content-Type',
      'Authorization',
      'X-Requested-With',
      'X-User-JWT',
      'X-User-Cookie',
      'x-service-auth',
      'x-request-id',
      // ⬇️ add your custom auth headers here
      'token',
      'X-Auth-Token',
    ],
  })
);

// Extra CORS headers & preflight handling (helps on some hosts/CDNs)
app.use((req, res, next) => {
  const origin = req.headers.origin;
  if (!origin || ALLOWED_ORIGINS.includes(origin)) {
    // reflect exact origin (required if credentials: true)
    res.setHeader('Access-Control-Allow-Origin', origin || ALLOWED_ORIGINS[0] || '*');
  }
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS');
  res.setHeader(
    'Access-Control-Allow-Headers',
    [
      'Content-Type',
      'Authorization',
      'X-Requested-With',
      'X-User-JWT',
      'X-User-Cookie',
      'x-service-auth',
      'x-request-id',
      // ⬇️ mirror the same additions here
      'token',
      'X-Auth-Token',
    ].join(', ')
  );
  // Let the browser read these headers in JS (handy for debug & checkout flows)
  res.setHeader(
    'Access-Control-Expose-Headers',
    [
      'X-Answer-Source',
      'X-Request-Id',
      'X-Checkout-Url',
      'X-Client-Secret',
      'X-Order-Id',
      'X-Echo-UserId',
    ].join(', ')
  );
  if (req.method === 'OPTIONS') return res.status(204).end();
  next();
});

/* -------- Stripe webhook: RAW body BEFORE express.json -------- */
app.post(
  '/api/order/webhook',
  express.raw({ type: 'application/json' }),
  handleStripeWebhook
);

/* ------------------- JSON body for other routes ------------------- */
app.use(express.json({ limit: '10kb' }));

/* ---------------------- Infra (DB / Cloud) ---------------------- */
connectDB();
connectCloudinary();

/* --------------------------- Routes ---------------------------- */
app.use('/api/food', foodRouter);
app.use('/api/user', userRouter);
app.use('/api/cart', cartRouter);
app.use('/api/order', orderRouter);
app.use('/api/chat', chatRouter); // proxies to FastAPI and forwards auth headers

/* --------------------------- Health ---------------------------- */
app.get('/', (_req, res) => res.send('API working'));

/* ----------------------- Error handling ------------------------ */
// Optional: normalize CORS errors to 403 instead of 500
app.use((err, _req, res, next) => {
  if (err?.message?.startsWith('CORS:')) {
    return res.status(403).json({ error: err.message });
  }
  next(err);
});

app.listen(port, () => {
  console.log(`Server started on http://localhost:${port}`);
});