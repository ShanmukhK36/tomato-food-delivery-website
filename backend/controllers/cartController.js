import userModel from '../models/userModel.js';

// add items to user cart
const addToCart = async (req, res) => {
  try {
    const qty = Number(req.body.quantity) || 1; // default = 1
    const userData = await userModel.findById(req.body.userId);
    const cartData = userData.cartData || {};

    if (!cartData[req.body.itemId]) {
      cartData[req.body.itemId] = qty;
    } else {
      cartData[req.body.itemId] += qty;
    }

    await userModel.findByIdAndUpdate(req.body.userId, { cartData });
    res.json({ success: true, message: `Added ${qty} item(s) to cart` });
  } catch (error) {
    console.error(error);
    res.json({ success: false, message: error.message });
  }
};

// remove items from user cart
const removeFromCart = async (req, res) => {
  try {
    let userData = await userModel.findById(req.body.userId);
    let cartData = await userData.cartData;

    if (cartData[req.body.itemId] > 0) {
      cartData[req.body.itemId] -= 1;
      // 🧹 If quantity becomes 0, delete the item entirely
      if (cartData[req.body.itemId] === 0) {
        delete cartData[req.body.itemId];
      }
    }

    await userModel.findByIdAndUpdate(req.body.userId, { cartData });
    res.json({ success: true, message: "Removed From Cart" });
  } catch (error) {
    console.log(error);
    return res.json({ success: false, message: error.message });
  }
};

// fetch user cart data
const getCart = async (req, res) => {
    try {
        let userData = await userModel.findById(req.body.userId);
        let cartData = await userData.cartData;
        res.json({success: true, cartData});
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

// add multiple items [{itemId, qty}] with per-item status
const addManyToCart = async (req, res) => {
  try {
    const { userId, itemId, quantity } = req.body;
    const userData = await userModel.findById(userId);
    const cartData = userData.cartData || {};

    for (let i = 0; i < quantity; i++) {
      if (!cartData[itemId]) {
        cartData[itemId] = 1;
      } else {
        cartData[itemId] += 1;
      }
    }

    await userModel.findByIdAndUpdate(userId, { cartData });
    res.json({ success: true, message: `Added ${quantity} item(s) to cart` });
  } catch (error) {
    console.error(error);
    res.json({ success: false, message: error.message });
  }
};

// remove multiple items [{itemId, qty}] with per-item status
const removeManyFromCart = async (req, res) => {
  try {
    const user = await userModel.findById(req.body.userId);
    const cartData = { ...(user.cartData || {}) };

    for (const { itemId, qty } of (req.body.items || [])) {
      const n = Math.max(0, (cartData[itemId] || 0) - (Number(qty) || 1));
      if (n <= 0) delete cartData[itemId];
      else cartData[itemId] = n;
    }

    await userModel.findByIdAndUpdate(req.body.userId, { cartData });
    res.json({ success: true, message: "Removed multiple items", cartData });
  } catch (error) {
    console.error(error);
    res.json({ success: false, message: error.message });
  }
};

export {addToCart, removeFromCart, getCart, addManyToCart, removeManyFromCart};