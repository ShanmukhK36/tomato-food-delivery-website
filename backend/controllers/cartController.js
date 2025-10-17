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
    const userId = req.body?.userId;
    const items = Array.isArray(req.body?.items) ? req.body.items : [];
    if (!userId) return res.json({ success: false, message: "userId required" });
    if (!items.length) return res.json({ success: false, message: "items array required" });

    const user = await userModel.findById(userId);
    if (!user) return res.json({ success: false, message: "user not found" });

    const cart = (user.cartData && typeof user.cartData === "object") ? user.cartData : {};

    const results = items.map((it) => {
      const itemId = it?.itemId;
      const rawQty = it?.qty ?? 1;
      const qty = Number.isFinite(Number(rawQty)) && Number(rawQty) > 0 ? Math.floor(Number(rawQty)) : 1;

      if (!itemId) {
        return { itemId: null, ok: false, code: "missing_itemId", message: "itemId required" };
      }
      const current = Number(cart[itemId] || 0);
      cart[itemId] = current + qty;

      return { itemId, ok: true, added: qty, newQty: cart[itemId] };
    });

    await userModel.findByIdAndUpdate(userId, { cartData: cart });

    return res.json({
      success: true,
      message: "Added items",
      results,
      cartData: cart,
    });
  } catch (error) {
    console.log(error);
    return res.json({ success: false, message: error.message });
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