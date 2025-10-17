import userModel from '../models/userModel.js';

// add items to user cart
const addToCart = async (req, res) => {
    try {
        let userData = await userModel.findById(req.body.userId);
        let cartData = await userData.cartData;
        if(!cartData[req.body.itemId]) {
            cartData[req.body.itemId] = 1;
        } else {
            cartData[req.body.itemId] += 1;
        }
        await userModel.findByIdAndUpdate(req.body.userId, {cartData});
        res.json({success: true, message: 'Added To Cart'});
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

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
    const { userId, itemId, quantity, items } = req.body;

    if (Array.isArray(items) && items.length > 0) {
      // multiple items [{itemId, qty}]
      for (const { itemId, qty } of items) {
        for (let i = 0; i < (Number(qty) || 1); i++) {
          await addToCart({ body: { userId, itemId } }, { json: () => {} });
        }
      }
    } else {
      // single item with quantity
      for (let i = 0; i < (Number(quantity) || 1); i++) {
        await addToCart({ body: { userId, itemId } }, { json: () => {} });
      }
    }

    res.json({ success: true, message: "Added multiple items to cart" });
  } catch (error) {
    console.error(error);
    res.json({ success: false, message: error.message });
  }
};

// remove multiple items [{itemId, qty}] with per-item status
const removeManyFromCart = async (req, res) => {
  try {
    const { userId, itemId, quantity, items } = req.body;

    if (Array.isArray(items) && items.length > 0) {
      for (const { itemId, qty } of items) {
        for (let i = 0; i < (Number(qty) || 1); i++) {
          await removeFromCart({ body: { userId, itemId } }, { json: () => {} });
        }
      }
    } else {
      for (let i = 0; i < (Number(quantity) || 1); i++) {
        await removeFromCart({ body: { userId, itemId } }, { json: () => {} });
      }
    }

    res.json({ success: true, message: "Removed multiple items from cart" });
  } catch (error) {
    console.error(error);
    res.json({ success: false, message: error.message });
  }
};

export {addToCart, removeFromCart, getCart, addManyToCart, removeManyFromCart};