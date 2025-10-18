import userModel from '../models/userModel.js';

// normalize quantity
const parseQty = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return 1;
  return Math.max(1, Math.floor(n));
};

// add items to user cart
const addToCart = async (req, res) => {
  try {
    const { userId, itemId } = req.body;
    if (!userId || !itemId) {
      return res.json({ success: false, message: 'userId and itemId are required' });
    }

    const qty = parseQty(req.body.qty); // default 1 if missing/invalid

    const user = await userModel.findById(userId);
    if (!user) return res.json({ success: false, message: 'User not found' });

    const cartData = user.cartData || {};
    cartData[itemId] = (cartData[itemId] || 0) + qty;

    await userModel.findByIdAndUpdate(userId, { cartData });
    return res.json({
      success: true,
      message: `Added ${qty} to cart`,
      itemId,
      qtyAdded: qty,
      newQty: cartData[itemId],
      cartData,
    });
  } catch (error) {
    console.error(error);
    return res.json({ success: false, message: error.message });
  }
};

// remove items from user cart
const removeFromCart = async (req, res) => {
  try {
    const { userId, itemId } = req.body;
    if (!userId || !itemId) {
      return res.json({ success: false, message: 'userId and itemId are required' });
    }

    const qty = parseQty(req.body.qty); // default 1 if missing/invalid

    const user = await userModel.findById(userId);
    if (!user) return res.json({ success: false, message: 'User not found' });

    const cartData = user.cartData || {};
    const current = Number(cartData[itemId] || 0);

    if (current <= 0) {
      // nothing to remove
      return res.json({
        success: true,
        message: 'Item not in cart',
        itemId,
        qtyRemoved: 0,
        newQty: 0,
        cartData,
      });
    }

    const newQty = current - qty;
    if (newQty <= 0) {
      delete cartData[itemId];
    } else {
      cartData[itemId] = newQty;
    }

    await userModel.findByIdAndUpdate(userId, { cartData });
    return res.json({
      success: true,
      message: `Removed ${Math.min(qty, current)} from cart`,
      itemId,
      qtyRemoved: Math.min(qty, current),
      newQty: Math.max(0, newQty),
      cartData,
    });
  } catch (error) {
    console.error(error);
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

export {addToCart, removeFromCart, getCart};