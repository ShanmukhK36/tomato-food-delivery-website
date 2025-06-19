import { useContext, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { StoreContext } from '../context/StoreContext';
import '../index.css';
import axios from 'axios';

const Verify = () => {
    const [searchParams, setSearchParams] = useSearchParams();
    const success = searchParams.get('success');
    const orderId = searchParams.get('orderId');
    const {url} = useContext(StoreContext);
    const navigate = useNavigate();
    const verifyPayment = async () => {
        const response = await axios.post(url+'/api/order/verify',{success, orderId});
        if(response.data.success) {
            navigate('/myorders');
        } else {
            navigate('/');
        }
    }
    useEffect(() => {
        verifyPayment();
    }, [])
  return (
    <div className='h-[60vh] grid '>
        <div className='spinner w-[100px] h-[100px] place-self-center rounded-full border-[5px] border-[#bdbdbd] border-t-orange-600'>

        </div>
    </div>
  )
}

export default Verify;