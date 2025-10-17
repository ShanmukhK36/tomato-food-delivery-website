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
        if(cartData[req.body.itemId] > 0) {
            cartData[req.body.itemId] -= 1;
        } 
        await userModel.findByIdAndUpdate(req.body.userId, {cartData});
        res.json({success: true, message: 'Removed From Cart'});
    } catch(error) {
        console.log(error);
        return res.json({success: false, message: error.message});
    }
}

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

      const present = Number(cart[itemId] || 0);
      if (!present) {
        return {
          itemId,
          ok: false,
          code: "not_in_cart",
          message: "Item is not in the cart",
          present: 0,
          requestedRemove: qty,
        };
      }
      if (qty > present) {
        return {
          itemId,
          ok: false,
          code: "insufficient_quantity",
          message: "Requested quantity to remove exceeds the quantity in cart",
          present,
          requestedRemove: qty,
        };
      }

      const newQty = present - qty;
      if (newQty <= 0) {
        delete cart[itemId];
      } else {
        cart[itemId] = newQty;
      }

      return { itemId, ok: true, removed: qty, newQty: cart[itemId] || 0 };
    });

    await userModel.findByIdAndUpdate(userId, { cartData: cart });

    const changed = results.some(r => r.ok);
    return res.json({
      success: changed,
      message: changed ? "Removed items" : "No items removed",
      results,
      cartData: cart,
    });
  } catch (error) {
    console.log(error);
    return res.json({ success: false, message: error.message });
  }
};

export {addToCart, removeFromCart, getCart, addManyToCart, removeManyFromCart};