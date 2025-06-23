import {v2 as cloudinary} from 'cloudinary';
import foodModel from "../models/foodModel.js";

// add food items
const addFood = async(req, res) => {
    const image = req.files.image && req.files.image[0];
    let result = await cloudinary.uploader.upload(image.path, {
      resource_type: 'image',
    });
    const image_url = result.secure_url;
    const food = new foodModel({
        name: req.body.name,
        description: req.body.description,
        price: req.body.price,
        category: req.body.category,
        image: image_url
    })
    try {
        await food.save();
        res.json({success: true, message: 'Food Item Added Successfully'});
    } catch(error) {
        console.log(error);
        res.json({success: false, message: error.message})
    }
}

// all food list
const foodList = async(req, res) => {
    try {
        const foods = await foodModel.find({});
        res.json({success: true, data: foods});
    } catch(error) {
        console.log(error);
        res.json({success: false, message: error.message});
    }
}

// remove food item
const removeFood = async(req, res) => {
    try {
        await foodModel.findByIdAndDelete(req.body.id);
        res.json({success: true, message: 'Food Removed'});
    } catch(error) {
        console.log(error);
        res.json({success: false, message: error.message});
    }
}

export {addFood, foodList, removeFood};