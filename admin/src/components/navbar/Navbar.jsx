import './Navbar.css';
import {assets} from '../../assets/assets';
import { Link } from 'react-router-dom';

const Navbar = () => {
  const frontend_url = import.meta.env.VITE_FRONTEND_URL;
  return (
    <div className='navbar'>
        <img className='logo' src={assets.logo} />
        <Link to={frontend_url}>
          <img className='profile' src={assets.profile_image} />
        </Link>
    </div>
  )
}

export default Navbar