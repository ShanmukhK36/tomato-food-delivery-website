import express from 'express';
import { addFood, foodList, removeFood } from '../controllers/foodController.js';
import upload from '../middleware/multer.js';

const foodRouter = express.Router();

foodRouter.post('/add', upload.single('image'), addFood);
foodRouter.get('/list', foodList);
foodRouter.post('/remove', removeFood);

export default foodRouter;