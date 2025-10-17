import express from 'express';
import { addToCart, removeFromCart, getCart, addManyToCart, removeManyFromCart } from '../controllers/cartController.js';
import authMiddleware from '../middleware/auth.js';

const cartRouter = express.Router();

cartRouter.post('/add', authMiddleware, addToCart);
cartRouter.post('/remove', authMiddleware, removeFromCart);
cartRouter.post('/get', authMiddleware, getCart);
cartRouter.post("/cart/add-many", authMiddleware, addManyToCart);
cartRouter.post("/cart/remove-many", authMiddleware, removeManyFromCart);

export default cartRouter;