import orderModel from "../models/orderModel.js";
import userModel from "../models/userModel.js";
import { Stripe } from 'stripe';

const stripe = new Stripe(process.env.STRIPE);

// Placing user order for frontend
const placeOrder = async (req, res) => {
    const frontend_url = process.env.FRONTEND_URL;
    try {
        const newOrder = new orderModel({
            userId: req.body.userId,
            items: req.body.items,
            amount: req.body.amount,
            address: req.body.address
        })
        await newOrder.save();
        await userModel.findByIdAndUpdate(req.body.userId, {cartData: {}});
        const line_items = req.body.items.map((item) => ({
            price_data: {
                currency: 'usd',
                product_data: {
                    name: item.name
                },
                unit_amount: item.price * 100
            },
            quantity: item.quantity
        }))
        line_items.push({
            price_data: {
                currency: 'usd',
                product_data: {
                    name: 'Delivery Charges'
                },
                unit_amount: 2*100
            },
            quantity: 1
        })
        console.log(line_items);
        const session = await stripe.checkout.sessions.create({
            line_items: line_items,
            mode: 'payment',
            success_url: `${frontend_url}/verify?success=true&orderId=${newOrder._id}`,
            cancel_url: `${frontend_url}/verify?success=false&orderId=${newOrder._id}`
        })
        res.json({success: true, session_url: session.url});
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

const verifyOrder = async (req, res) => {
    try {
        if(success === 'true') {
            await orderModel.findByIdAndUpdate(orderId, {payment : true});
            res.json({success: true, message: 'Payment Successful'});
        } else {
            await orderModel.findByIdAndDelete(orderId);
            res.json({success: false, message: 'Payment Unsuccessful'});
        }
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

const userOrders = async (req, res) => {
    try {
        const orders = await orderModel.find({userId: req.body.userId});
        res.json({success: true, data: orders})
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

// Listing orders for admin panel
const listOrders = async (req, res) => {
    try {
        const orders = await orderModel.find({});
        res.json({success: true, orders});
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

// api for updating order status
const updateStatus = async (req, res) => {
    try {
        await orderModel.findByIdAndUpdate(req.body.orderId, {status: req.body.status});
        res.json({success: true, message: 'Status Updated'});
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

export {placeOrder, verifyOrder, userOrders, listOrders, updateStatus};