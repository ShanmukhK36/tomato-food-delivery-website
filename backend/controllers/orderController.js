import orderModel from "../models/orderModel.js";
import userModel from "../models/userModel.js";
import { Stripe } from "stripe";

const stripe = new Stripe(process.env.STRIPE, {
  apiVersion: "2024-06-20", // pin your version
});

// Create Checkout Session (do NOT clear cart here)
const placeOrder = async (req, res) => {
  const frontend_url = process.env.FRONTEND_URL;
  try {
    const { userId, items, amount, address } = req.body;

    // Create order first (tie Stripe session to this _id)
    const newOrder = await orderModel.create({
      userId,
      items,
      amount,
      address,
      payment: false,
      // paymentInfo will be set on final outcome only
    });

    // Build Stripe line items
    const line_items = items.map((item) => ({
      price_data: {
        currency: "usd",
        product_data: { name: item.name },
        unit_amount: Math.round(item.price * 100),
      },
      quantity: item.quantity,
    }));

    // Optional: delivery fee line
    line_items.push({
      price_data: {
        currency: "usd",
        product_data: { name: "Delivery Charges" },
        unit_amount: 2 * 100,
      },
      quantity: 1,
    });

    // Create Checkout Session
    const session = await stripe.checkout.sessions.create({
      line_items,
      mode: "payment",
      client_reference_id: String(newOrder._id),
      metadata: {
        orderId: String(newOrder._id),
        userId: String(userId),
      },
      success_url: `${frontend_url}/verify?orderId=${newOrder._id}&session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${frontend_url}/verify?orderId=${newOrder._id}&session_id={CHECKOUT_SESSION_ID}`,
    });

    // Persist Stripe session id (handy for reconciliation)
    await orderModel.findByIdAndUpdate(newOrder._id, {
      $set: { "paymentInfo.stripe.sessionId": session.id },
    });

    return res.json({ success: true, session_url: session.url });
  } catch (error) {
    console.error("[placeOrder] error:", error);
    return res.json({ success: false, message: error.message });
  }
};

// UX helper: on redirect, check real status with Stripe
// Anything not 'paid' is treated as final failure here.
const verifyOrder = async (req, res) => {
  try {
    const { orderId, session_id } = req.query;
    if (!orderId || !session_id) {
      return res.json({ success: false, message: "Missing orderId or session_id" });
    }

    // Fetch session and expand PI for detailed error/charge
    const session = await stripe.checkout.sessions.retrieve(session_id, {
      expand: ["payment_intent.latest_charge"],
    });

    const paymentIntent = session.payment_intent;
    const paymentStatus = session.payment_status; // 'paid' | 'unpaid' | 'no_payment_required'

    if (paymentStatus === "paid") {
      const chargeId =
        paymentIntent?.latest_charge && typeof paymentIntent.latest_charge === "object"
          ? paymentIntent.latest_charge.id
          : "";

      await orderModel.findByIdAndUpdate(orderId, {
        $set: {
          payment: true,
          status: "PAID",
          "paymentInfo.status": "succeeded",
          "paymentInfo.successMessage": "Payment succeeded.",
          "paymentInfo.stripe.paymentIntentId": paymentIntent?.id || "",
          "paymentInfo.stripe.chargeId": chargeId,
          "paymentInfo.paidAt": new Date(),
        },
      });

      return res.json({ success: true, message: "Payment Successful" });
    }

    // Treat all other cases as final failure (from UI perspective)
    let errorCode = "";
    let errorMessage = "";

    if (paymentIntent?.last_payment_error) {
      errorCode = paymentIntent.last_payment_error.code || "";
      errorMessage = paymentIntent.last_payment_error.message || "Payment failed.";
    }
    if (!errorCode) {
      errorCode = "user_canceled_or_incomplete";
      errorMessage = "Payment not completed.";
    }

    await orderModel.findByIdAndUpdate(orderId, {
      $set: {
        payment: false,
        status: "Payment Failed",
        "paymentInfo.status": "failed",
        "paymentInfo.errorCode": errorCode,
        "paymentInfo.errorMessage": errorMessage,
        "paymentInfo.failedAt": new Date(),
      },
    });

    return res.json({ success: false, message: "Payment Unsuccessful" });
  } catch (error) {
    console.error("[verifyOrder] error:", error);
    return res.json({ success: false, message: error.message });
  }
};

// Stripe webhook: authoritative final outcomes
const handleStripeWebhook = async (req, res) => {
  const sig = req.headers["stripe-signature"];
  const endpointSecret = process.env.STRIPE_WEBHOOK_SECRET;

  let event;
  try {
    // IMPORTANT: this route must receive raw body (see routing snippet below)
    event = stripe.webhooks.constructEvent(req.body, sig, endpointSecret);
  } catch (err) {
    console.error("[webhook] signature verify failed:", err.message);
    return res.status(400).send(`Webhook Error: ${err.message}`);
  }

  try {
    switch (event.type) {
      // Final Success
      case "checkout.session.completed": {
        const session = event.data.object;
        const orderId = session.metadata?.orderId || session.client_reference_id;

        // Expand PI to capture charge id
        const full = await stripe.checkout.sessions.retrieve(session.id, {
          expand: ["payment_intent.latest_charge"],
        });
        const pi = full.payment_intent;
        const chargeId =
          pi?.latest_charge && typeof pi.latest_charge === "object" ? pi.latest_charge.id : "";

        if (orderId) {
          const updated = await orderModel.findByIdAndUpdate(
            orderId,
            {
              $set: {
                payment: true,
                status: "PAID",
                "paymentInfo.status": "succeeded",
                "paymentInfo.successMessage": "Payment succeeded.",
                "paymentInfo.stripe.sessionId": session.id,
                "paymentInfo.stripe.paymentIntentId": pi?.id || "",
                "paymentInfo.stripe.chargeId": chargeId,
                "paymentInfo.paidAt": new Date(),
              },
            },
            { new: true }
          );

          // ✅ Clear cart now that payment is confirmed
          if (updated?.userId) {
            await userModel.findByIdAndUpdate(updated.userId, { cartData: {} });
          }
        }
        break;
      }

      // Final Failure (gateway failure)
      case "payment_intent.payment_failed": {
        const pi = event.data.object;
        const orderId = pi.metadata?.orderId;

        const errorCode = pi.last_payment_error?.code || "payment_failed";
        const errorMessage = pi.last_payment_error?.message || "Payment failed.";

        if (orderId) {
          await orderModel.findByIdAndUpdate(orderId, {
            $set: {
              payment: false,
              status: "Payment Failed",
              "paymentInfo.status": "failed",
              "paymentInfo.errorCode": errorCode,
              "paymentInfo.errorMessage": errorMessage,
              "paymentInfo.stripe.paymentIntentId": pi.id,
              "paymentInfo.failedAt": new Date(),
            },
          });
        }
        break;
      }

      // Treat session expiration as final failure
      case "checkout.session.expired": {
        const session = event.data.object;
        const orderId = session.metadata?.orderId || session.client_reference_id;

        if (orderId) {
          await orderModel.findByIdAndUpdate(orderId, {
            $set: {
              payment: false,
              status: "Payment Failed",
              "paymentInfo.status": "failed",
              "paymentInfo.errorCode": "session_expired",
              "paymentInfo.errorMessage": "Checkout session expired before completion.",
              "paymentInfo.stripe.sessionId": session.id,
              "paymentInfo.failedAt": new Date(),
            },
          });
        }
        break;
      }

      default:
        // no-op for other events
        break;
    }

    return res.json({ received: true });
  } catch (err) {
    console.error("[webhook] handler error:", err);
    return res.status(500).send("Webhook handler error");
  }
};

// User orders
const userOrders = async (req, res) => {
  try {
    const orders = await orderModel
      .find({
        userId: req.body.userId,
        payment: true,
        "paymentInfo.status": "succeeded",
      })
      .sort({ date: -1 });

    res.json({ success: true, data: orders });
  } catch (error) {
    console.error("[userOrders] error:", error);
    return res.json({ success: false, message: error.message });
  }
};

// Admin list
const listOrders = async (req, res) => {
  try {
    const orders = await orderModel
      .find({
        payment: true,
        "paymentInfo.status": "succeeded",
      })
      .sort({ date: -1 });

    res.json({ success: true, orders });
  } catch (error) {
    console.error("[listOrders] error:", error);
    return res.json({ success: false, message: error.message });
  }
};

// Admin status update
const updateStatus = async (req, res) => {
  try {
    const { orderId, status } = req.body;

    // Ensure the order exists AND is a successful payment
    const order = await orderModel.findOne({
      _id: orderId,
      payment: true,
      "paymentInfo.status": "succeeded",
    });

    if (!order) {
      return res.json({
        success: false,
        message: "Order not found or not a successful payment",
      });
    }

    await orderModel.findByIdAndUpdate(orderId, { status });
    res.json({ success: true, message: "Status Updated" });
  } catch (error) {
    console.error("[updateStatus] error:", error);
    return res.json({ success: false, message: error.message });
  }
};

export { placeOrder, verifyOrder, userOrders, listOrders, updateStatus, handleStripeWebhook};