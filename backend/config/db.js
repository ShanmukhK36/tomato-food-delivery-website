import mongoose from 'mongoose';

export const connectDB = async() => {
    (await mongoose.connect('mongodb+srv://admin:admin@cluster0.yktkthp.mongodb.net/food-delivery').then(() => console.log("DB Connected")));
}